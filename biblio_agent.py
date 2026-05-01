#!/usr/bin/env python3
"""
Agente de búsqueda bibliográfica científica.

Modos de uso:
  1. Búsqueda por tema/hipótesis
  2. Análisis de un paper (PDF o DOI) + búsqueda de literatura relacionada
"""

import os
import re
import sys
import time
import argparse
import requests
import anthropic
import fitz  # pymupdf
from datetime import datetime
from scholarly import scholarly

# Carga .env si existe (para ANTHROPIC_API_KEY)
_env_file = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env_file):
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                _key, _val = _k.strip(), _v.strip()
                if _val:
                    os.environ[_key] = _val

# ── Prompts ───────────────────────────────────────────────────────────────────

BIBLIO_SYSTEM_PROMPT = """
# ROLE
You are an expert scientific bibliographic agent specializing in molecular biology,
microbiology, and bioinformatics. Your mission is to find and critically evaluate
scientific literature that supports or challenges a given hypothesis.

# RULES
- Only retrieve references from journals with an Impact Factor (IF) > 4.
- Always prioritize journals by descending Impact Factor.
- Prioritize recent articles (last 5 years), UNLESS the topic requires citing
  the original/foundational paper that first demonstrated a concept.
- Always specify the publication type: [ARTICLE] or [REVIEW].
- All responses must be in English, regardless of the language of the query.
- Always provide a minimum of 5 references per query.
- Be critical: if the hypothesis is flawed, partially supported, or contradicted
  by the literature, say so clearly and explain why.
- Be concise: each reference must include a brief summary (2-4 sentences max).

# OUTPUT FORMAT
For each reference use this structure:

[NUMBER]. [ARTICLE/REVIEW]
**Title:** Full title of the paper
**Authors:** First author et al.
**Journal:** Journal name (IF: X.X)
**Year:** YYYY
**DOI:** doi link if available
**Summary:** Brief critical summary of the paper and its relevance to the hypothesis.

---
## Critical Assessment
After the reference list, always include a short paragraph with your honest
critical evaluation:
- Is the hypothesis well-supported by current literature?
- Are there contradictory findings?
- What are the main gaps or limitations in the evidence?

# BEHAVIOR
- If the hypothesis is ambiguous, ask for clarification before searching.
- If fewer than 5 papers with IF > 4 exist on the topic, notify the user
  and explain why coverage is limited.
- Never fabricate references. If unsure about a DOI or year, flag it with [VERIFY].
"""

ANALYSIS_SYSTEM_PROMPT = """
You are an expert scientific analyst. Given the full text or abstract of a scientific paper,
your task is to extract structured information to guide a bibliographic search.

Return ONLY a JSON object with this exact structure (no markdown, no explanation):
{
  "title": "Full title of the paper",
  "authors": "First author et al.",
  "year": "YYYY",
  "journal": "Journal name",
  "main_hypothesis": "The central hypothesis or claim of the paper in one sentence",
  "key_claims": [
    "Specific claim 1 that could be verified with literature",
    "Specific claim 2",
    "Specific claim 3"
  ],
  "search_queries": [
    "Optimized search query 1 for bibliographic databases",
    "Optimized search query 2",
    "Optimized search query 3"
  ],
  "summary": "2-3 sentence summary of what the paper does and claims"
}

Be precise. search_queries should be specific enough to find relevant literature,
not just the paper title.
"""

# ── Semantic Scholar ──────────────────────────────────────────────────────────

S2_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_FIELDS = "title,authors,year,externalIds,publicationTypes,journal,abstract,citationCount"
S2_DOI_URL = "https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"


def search_semantic_scholar(query: str, limit: int = 25) -> list[dict]:
    params = {"query": query, "limit": limit, "fields": S2_FIELDS}
    for attempt in range(3):
        try:
            r = requests.get(S2_URL, params=params, timeout=15)
            if r.status_code == 429:
                wait = 10 * (attempt + 1)
                print(f"[S2 rate limit] Waiting {wait}s...", file=sys.stderr)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json().get("data", [])
        except requests.RequestException as e:
            print(f"[Warning] Semantic Scholar (attempt {attempt+1}): {e}", file=sys.stderr)
            time.sleep(5)
    return []


def fetch_s2_by_doi(doi: str) -> dict | None:
    """Obtiene metadatos de un paper concreto por DOI desde Semantic Scholar."""
    try:
        r = requests.get(
            S2_DOI_URL.format(doi=doi),
            params={"fields": S2_FIELDS + ",tldr"},
            timeout=15,
        )
        if r.status_code == 200:
            return r.json()
    except requests.RequestException:
        pass
    return None


def format_s2_papers(papers: list[dict]) -> str:
    if not papers:
        return "No papers found in Semantic Scholar."
    lines = ["Here are the papers retrieved from Semantic Scholar:\n"]
    for i, p in enumerate(papers, 1):
        authors = p.get("authors", [])
        first_author = authors[0].get("name", "Unknown") if authors else "Unknown"
        journal = p.get("journal") or {}
        journal_name = journal.get("name", "Unknown journal") if journal else "Unknown journal"
        pub_types = p.get("publicationTypes") or []
        pub_type = "REVIEW" if "Review" in pub_types else "ARTICLE"
        abstract = p.get("abstract") or "No abstract available."
        doi = (p.get("externalIds") or {}).get("DOI")
        doi_str = f"https://doi.org/{doi}" if doi else "N/A"
        lines.append(
            f"{i}. [{pub_type}]\n"
            f"   Title: {p.get('title', 'N/A')}\n"
            f"   Authors: {first_author} et al.\n"
            f"   Journal: {journal_name}\n"
            f"   Year: {p.get('year', 'N/A')}\n"
            f"   DOI: {doi_str}\n"
            f"   Citations: {p.get('citationCount', 0)}\n"
            f"   Abstract: {abstract[:400]}{'...' if len(abstract) > 400 else ''}\n"
        )
    return "\n".join(lines)


# ── Google Scholar ────────────────────────────────────────────────────────────

def search_google_scholar(query: str, limit: int = 25) -> list[dict]:
    results = []
    try:
        search_gen = scholarly.search_pubs(query)
        for _ in range(limit):
            try:
                pub = next(search_gen)
                bib = pub.get("bib", {})
                results.append({
                    "title": bib.get("title", "N/A"),
                    "authors": bib.get("author", []),
                    "year": bib.get("pub_year", "N/A"),
                    "journal": bib.get("venue", "Unknown journal"),
                    "abstract": bib.get("abstract", "No abstract available."),
                    "citations": pub.get("num_citations", 0),
                    "url": pub.get("pub_url") or pub.get("eprint_url") or "N/A",
                })
            except StopIteration:
                break
    except Exception as e:
        print(f"[Warning] Google Scholar: {e}", file=sys.stderr)
    return results


def format_gs_papers(papers: list[dict]) -> str:
    if not papers:
        return "No papers found in Google Scholar."
    lines = ["Here are the papers retrieved from Google Scholar:\n"]
    for i, p in enumerate(papers, 1):
        authors = p.get("authors") or []
        first_author = authors[0] if isinstance(authors, list) and authors else str(authors)
        abstract = p.get("abstract") or "No abstract available."
        lines.append(
            f"{i}. [ARTICLE]\n"
            f"   Title: {p.get('title', 'N/A')}\n"
            f"   Authors: {first_author} et al.\n"
            f"   Journal/Venue: {p.get('journal', 'Unknown')}\n"
            f"   Year: {p.get('year', 'N/A')}\n"
            f"   URL: {p.get('url', 'N/A')}\n"
            f"   Citations: {p.get('citations', 0)}\n"
            f"   Abstract: {abstract[:400]}{'...' if len(abstract) > 400 else ''}\n"
        )
    return "\n".join(lines)


# ── Paper input ───────────────────────────────────────────────────────────────

def extract_pdf_text(path: str, max_chars: int = 8000) -> str:
    """Extrae texto de un PDF (primeras páginas hasta max_chars)."""
    doc = fitz.open(path)
    text = ""
    for page in doc:
        text += page.get_text()
        if len(text) >= max_chars:
            break
    return text[:max_chars]


def fetch_doi_abstract(doi: str) -> str:
    """Obtiene el abstract de un DOI via Semantic Scholar."""
    data = fetch_s2_by_doi(doi)
    if data:
        parts = [
            f"Title: {data.get('title', 'N/A')}",
            f"Authors: {', '.join(a['name'] for a in data.get('authors', [])[:3])}",
            f"Year: {data.get('year', 'N/A')}",
            f"Journal: {(data.get('journal') or {}).get('name', 'N/A')}",
            f"Abstract: {data.get('abstract') or 'Not available'}",
        ]
        tldr = data.get("tldr")
        if tldr:
            parts.append(f"TL;DR: {tldr.get('text', '')}")
        return "\n".join(parts)
    return ""


def analyze_paper(client: anthropic.Anthropic, paper_text: str) -> dict:
    """Usa Claude para analizar el paper y extraer hipótesis y queries de búsqueda."""
    import json

    print("Analyzing paper with Claude...", end=" ", flush=True)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=ANALYSIS_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"Analyze this paper and return the JSON:\n\n{paper_text}"
        }],
    )
    print("done.")

    raw = response.content[0].text.strip()
    # Limpia posibles bloques de código markdown
    raw = re.sub(r"^```(?:json)?\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print("[Warning] Could not parse analysis JSON. Using raw text.", file=sys.stderr)
        return {
            "title": "Unknown",
            "main_hypothesis": paper_text[:200],
            "key_claims": [],
            "search_queries": [paper_text[:100]],
            "summary": "",
        }


# ── Core search + eval ────────────────────────────────────────────────────────

def search_and_format(query: str, source: str) -> tuple[list[dict], str, str]:
    if source == "semantic":
        papers = search_semantic_scholar(query)
        return papers, format_s2_papers(papers), "Semantic Scholar"
    else:
        papers = search_google_scholar(query)
        return papers, format_gs_papers(papers), "Google Scholar"


def save_report(content: str, header: str, slug: str) -> str:
    output_dir = os.path.join(os.path.dirname(__file__), "reports")
    os.makedirs(output_dir, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"{date_str}_{slug[:60]}.md"
    filepath = os.path.join(output_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(header + content)
    return filepath


# ── Modes ─────────────────────────────────────────────────────────────────────

def mode_search(client: anthropic.Anthropic, hypothesis: str, source: str) -> None:
    papers, papers_text, source_label = search_and_format(hypothesis, source)
    print(f"Found {len(papers)} papers. Evaluating with Claude...\n")
    print("=" * 70)

    user_message = (
        f"Hypothesis to evaluate:\n{hypothesis}\n\n"
        f"{papers_text}\n\n"
        f"Source: {source_label}. Using the papers above as your primary source, "
        "evaluate the hypothesis. Select the most relevant papers (IF > 4 only), "
        "fill in Impact Factors and DOIs from your knowledge, and produce the "
        "structured output as instructed. Never invent papers not listed above. "
        "If a journal IF or DOI is unknown to you, mark it [VERIFY IF] or [VERIFY DOI]."
    )

    collected = []
    with client.messages.stream(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=BIBLIO_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    ) as stream:
        for text in stream.text_stream:
            print(text, end="", flush=True)
            collected.append(text)

    print("\n" + "=" * 70)

    slug = re.sub(r"[^\w\s-]", "", hypothesis.lower())
    slug = re.sub(r"\s+", "_", slug.strip())
    header = (
        f"# Bibliographic Report\n\n"
        f"**Hypothesis:** {hypothesis}\n\n"
        f"**Source:** {source_label}\n\n"
        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        f"**Papers retrieved:** {len(papers)}\n\n"
        f"---\n\n"
    )
    filepath = save_report("".join(collected), header, slug)
    print(f"\nReport saved to: {filepath}")


def mode_paper(client: anthropic.Anthropic, paper_input: str, source: str) -> None:
    # Cargar el contenido del paper
    if os.path.isfile(paper_input) and paper_input.endswith(".pdf"):
        print(f"Reading PDF: {paper_input}")
        paper_text = extract_pdf_text(paper_input)
    elif paper_input.startswith("10."):  # DOI
        print(f"Fetching DOI: {paper_input}")
        paper_text = fetch_doi_abstract(paper_input)
        if not paper_text:
            print("Could not fetch paper from DOI. Provide the abstract manually:")
            paper_text = sys.stdin.read()
    else:
        paper_text = paper_input

    if not paper_text.strip():
        print("Error: no paper content to analyze.", file=sys.stderr)
        sys.exit(1)

    # Paso 1: Claude extrae la hipótesis principal
    analysis = analyze_paper(client, paper_text)

    hypothesis = analysis.get("main_hypothesis", "").strip()
    if not hypothesis:
        print("Could not extract hypothesis from paper.", file=sys.stderr)
        sys.exit(1)

    print(f"\n{'=' * 70}")
    print(f"PAPER: {analysis.get('title', 'Unknown')}")
    print(f"AUTHORS: {analysis.get('authors', 'Unknown')}")
    print(f"EXTRACTED HYPOTHESIS: {hypothesis}")
    print("=" * 70)

    # Paso 2: búsqueda bibliográfica sobre esa hipótesis (igual que modo search)
    mode_search(client, hypothesis, source)


# ── Interactive menus ─────────────────────────────────────────────────────────

def choose_mode() -> str:
    print("\nSelect mode:")
    print("  1. Search by topic or hypothesis")
    print("  2. Analyze a paper (PDF, DOI, or paste text)")
    while True:
        choice = input("Choice [1/2]: ").strip()
        if choice == "1":
            return "search"
        if choice == "2":
            return "paper"
        print("Enter 1 or 2.")


def choose_source() -> str:
    print("\nSelect search source:")
    print("  1. Semantic Scholar  (structured data, DOIs, no scraping)")
    print("  2. Google Scholar    (broader coverage, scraping)")
    while True:
        choice = input("Choice [1/2]: ").strip()
        if choice == "1":
            return "semantic"
        if choice == "2":
            return "google"
        print("Enter 1 or 2.")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bibliographic Search Agent",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # Interactive mode\n"
            "  python biblio_agent.py\n\n"
            "  # Search by hypothesis\n"
            "  python biblio_agent.py --mode search --source semantic \"CRISPR off-target effects\"\n\n"
            "  # Analyze a PDF\n"
            "  python biblio_agent.py --mode paper --source google paper.pdf\n\n"
            "  # Analyze by DOI\n"
            "  python biblio_agent.py --mode paper --source semantic 10.1038/s41586-023-06459-4\n"
        ),
    )
    parser.add_argument("input", nargs="*", help="Hypothesis, PDF path, or DOI")
    parser.add_argument("--mode", choices=["search", "paper"], help="'search' or 'paper'")
    parser.add_argument("--source", choices=["semantic", "google"], help="Search source")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    client = anthropic.Anthropic(api_key=api_key, base_url="https://api.anthropic.com")

    mode = args.mode or choose_mode()
    source = args.source or choose_source()

    if mode == "search":
        if args.input:
            hypothesis = " ".join(args.input)
        else:
            print("\nEnter your hypothesis or research question:")
            hypothesis = input("> ").strip()
            if not hypothesis:
                print("No input provided. Exiting.")
                sys.exit(1)
        mode_search(client, hypothesis, source)

    else:  # paper
        if args.input:
            paper_input = " ".join(args.input)
        else:
            print("\nEnter PDF path, DOI (e.g. 10.1038/...), or paste abstract (Ctrl+D to finish):")
            choice = input("[path/DOI/text]: ").strip()
            if os.path.isfile(choice) or choice.startswith("10."):
                paper_input = choice
            else:
                # Asumir que es texto — leer el resto de stdin
                print("Paste the abstract/text and press Ctrl+D when done:")
                paper_input = choice + "\n" + sys.stdin.read()
        mode_paper(client, paper_input, source)


if __name__ == "__main__":
    main()
