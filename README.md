# Martita — Agente de Búsqueda Bibliográfica

Herramienta de línea de comandos que busca y evalúa literatura científica usando IA.

## Qué hace

Dado un tema, hipótesis o paper científico:

1. Busca papers relevantes en **Semantic Scholar** o **Google Scholar**
2. Filtra automáticamente por revistas con **Impact Factor > 4**
3. Genera una **evaluación crítica** con Claude sobre si la hipótesis está soportada, contradicha o tiene lagunas
4. Guarda el resultado como un **archivo `.md`** en la carpeta `reports/`

## Requisitos

- Python 3.10 o superior
- Una API key de Anthropic (conseguirla en https://console.anthropic.com)

## Instalación

```bash
git clone https://github.com/josevelaz-prog/Martita-la-lista.git
cd Martita-la-lista
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
```

## Uso

### Modo interactivo (recomendado)
```bash
python biblio_agent.py
```
El programa pregunta el modo, la fuente y la hipótesis paso a paso.

### Búsqueda directa por hipótesis
```bash
python biblio_agent.py --mode search --source semantic "CRISPR off-target effects limit gene therapy"
python biblio_agent.py --mode search --source google "gut microbiome and type 2 diabetes"
```

### Analizar un paper existente
El programa extrae la hipótesis del paper y lanza la búsqueda automáticamente.

```bash
# Desde un PDF
python biblio_agent.py --mode paper --source semantic articulo.pdf

# Desde un DOI
python biblio_agent.py --mode paper --source google 10.1038/s41586-023-06459-4
```

## Fuentes de búsqueda

| Fuente | Ventajas | Inconvenientes |
|---|---|---|
| Semantic Scholar | DOIs reales, datos estructurados, sin scraping | Puede dar rate limit con uso intensivo |
| Google Scholar | Mayor cobertura, encuentra preprints | Scraping, puede bloquearse |

## Resultado

Cada búsqueda genera un archivo `.md` en `reports/` con:
- Listado de referencias con título, autores, revista, año, DOI y resumen
- Evaluación crítica final: ¿está la hipótesis bien soportada? ¿hay contradicciones? ¿qué falta?
