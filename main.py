# main.py
from fastapi import FastAPI, File, UploadFile, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import requests
import time
import re
import os
import uuid
from datetime import datetime
from urllib.parse import quote
import json
import warnings
import asyncio
from typing import Optional

warnings.simplefilter(action='ignore', category=FutureWarning)

app = FastAPI(title="Processador de Bibliografia", version="1.0.0")

# Configurar CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Diretórios para arquivos
UPLOAD_DIR = "uploads"
PROCESSED_DIR = "processed"
CACHE_DIR = "cache"

# Criar diretórios se não existirem
for directory in [UPLOAD_DIR, PROCESSED_DIR, CACHE_DIR]:
    os.makedirs(directory, exist_ok=True)

# Dicionário para armazenar status de processamento
processing_status = {}

# Cache global
cache_buscas = {}

# Carregar cache se existir
try:
    with open(f"{CACHE_DIR}/cache_buscas.json", "r", encoding="utf-8") as f:
        cache_buscas = json.load(f)
except:
    pass

def limpar_texto(texto):
    """Limpa e normaliza texto"""
    if pd.isna(texto):
        return ""
    texto = str(texto).strip()
    texto = re.sub(r'[^\w\s]', ' ', texto)
    texto = re.sub(r'\s+', ' ', texto)
    return texto.strip()

def buscar_info_livro(titulo, autor=None, debug=False):
    """Busca informações detalhadas do livro incluindo ISBN e tipo de publicação"""
    if pd.isna(titulo):
        return None
    
    # Cache
    cache_key = f"{titulo}_{autor}"
    if cache_key in cache_buscas:
        return cache_buscas[cache_key]
    
    titulo_limpo = limpar_texto(titulo)
    query_parts = [f'intitle:{quote(titulo_limpo)}']
    
    if autor and not pd.isna(autor):
        autor_limpo = limpar_texto(autor)
        primeiro_autor = autor_limpo.split(';')[0].split(',')[0].strip()
        if primeiro_autor:
            query_parts.append(f'inauthor:{quote(primeiro_autor)}')
    
    query = '+'.join(query_parts)
    url = f"https://www.googleapis.com/books/v1/volumes?q={query}&maxResults=5"
    
    try:
        resposta = requests.get(url, timeout=10)
        dados = resposta.json()
        
        if 'items' in dados and len(dados['items']) > 0:
            for item in dados['items']:
                volume_info = item.get('volumeInfo', {})
                
                isbn = None
                for identifier in volume_info.get('industryIdentifiers', []):
                    if identifier['type'] == 'ISBN_13':
                        isbn = identifier['identifier']
                        break
                    elif identifier['type'] == 'ISBN_10':
                        isbn = identifier['identifier']
                
                tipo_citacao = identificar_tipo_citacao(volume_info)
                
                resultado = {
                    'isbn': isbn,
                    'tipo_citacao': tipo_citacao,
                    'titulo_google': volume_info.get('title', ''),
                    'subtitulo': volume_info.get('subtitle', ''),
                    'autores': ', '.join(volume_info.get('authors', [])),
                    'editora': volume_info.get('publisher', ''),
                    'ano_publicacao': volume_info.get('publishedDate', '')[:4] if volume_info.get('publishedDate') else '',
                    'paginas': volume_info.get('pageCount', ''),
                    'categorias': ', '.join(volume_info.get('categories', [])),
                    'idioma': volume_info.get('language', ''),
                    'print_type': volume_info.get('printType', ''),
                    'is_ebook': item.get('saleInfo', {}).get('isEbook', False)
                }
                
                # Salva no cache
                cache_buscas[cache_key] = resultado
                return resultado
                
    except Exception as e:
        print(f"Erro na busca: {e}")
    
    cache_buscas[cache_key] = None
    return None

def identificar_tipo_citacao(volume_info):
    """Identifica o tipo de citação baseado nas informações do volume"""
    categorias = volume_info.get('categories', [])
    titulo = volume_info.get('title', '').lower()
    descricao = volume_info.get('description', '').lower()
    
    palavras_academicas = ['dissertação', 'tese', 'monografia', 'trabalho de conclusão', 
                          'tcc', 'dissertation', 'thesis', 'doctoral', 'mestrado', 'doutorado']
    
    for palavra in palavras_academicas:
        if palavra in titulo or palavra in descricao:
            return 'Trabalho acadêmico'
    
    page_count = volume_info.get('pageCount', 0)
    if page_count and page_count < 50:
        for cat in categorias:
            if 'journal' in cat.lower() or 'article' in cat.lower() or 'revista' in cat.lower():
                return 'Artigo'
    
    if 'capítulo' in titulo or 'chapter' in titulo:
        return 'Capítulo de livro'
    
    return 'Livro'

def preencher_colunas_por_tipo(row, info_livro):
    """Preenche as colunas apropriadas baseado no tipo de citação"""
    if not info_livro:
        return row
    
    row['Isbn'] = info_livro.get('isbn', '')
    
    tipo = info_livro.get('tipo_citacao', 'Livro')
    row['Tipo Citação (obrigatório)'] = tipo
    
    if info_livro.get('subtitulo'):
        row['Subtítulo'] = info_livro['subtitulo']
    
    if info_livro.get('ano_publicacao'):
        row['Ano (apenas números)'] = info_livro['ano_publicacao']
    
    if info_livro.get('editora') and pd.isna(row.get('Editora')):
        row['Editora'] = info_livro['editora']
    
    if tipo == 'Capítulo de livro':
        if 'Título do Capítulo' not in row or pd.isna(row['Título do Capítulo']):
            row['Título do Capítulo'] = row.get('Título', '')
            row['Título'] = info_livro.get('titulo_google', '')
    
    elif tipo == 'Artigo':
        if 'Nome do artigo' not in row or pd.isna(row['Nome do artigo']):
            row['Nome do artigo'] = row.get('Título', '')
        
        categorias = info_livro.get('categorias', '')
        if categorias and 'Nome da Revista' not in row:
            row['Nome da Revista'] = categorias
        
        if info_livro.get('paginas'):
            row['Página inicial e final do artigo'] = f"1-{info_livro['paginas']}"
    
    elif tipo == 'Trabalho acadêmico':
        if info_livro.get('ano_publicacao'):
            row['Ano de entrega'] = info_livro['ano_publicacao']
            row['Ano de apresentação'] = info_livro['ano_publicacao']
        
        if info_livro.get('paginas'):
            row['Número de folhas'] = info_livro['paginas']
        
        titulo_lower = row.get('Título', '').lower()
        if 'dissertação' in titulo_lower or 'mestrado' in titulo_lower:
            row['Tipo de Trabalho'] = 'Dissertação de Mestrado'
        elif 'tese' in titulo_lower or 'doutorado' in titulo_lower:
            row['Tipo de Trabalho'] = 'Tese de Doutorado'
        else:
            row['Tipo de Trabalho'] = 'Trabalho de Conclusão de Curso'
    
    if info_livro.get('is_ebook'):
        row['É ebook (escreva SIM ou deixe em branco)'] = 'SIM'
    
    return row

def processar_leis(df):
    """Identifica e processa registros que são leis"""
    palavras_lei = ['lei', 'decreto', 'portaria', 'resolução', 'medida provisória', 
                    'constituição', 'código', 'estatuto', 'norma', 'regulamento']
    
    for idx, row in df.iterrows():
        titulo = str(row.get('Título', '')).lower()
        
        if any(palavra in titulo for palavra in palavras_lei):
            df.at[idx, 'Tipo Citação (obrigatório)'] = 'Lei'
            
            match = re.search(r'(lei|decreto|portaria|resolução)\s*n[º°]?\s*([\d\.]+)', titulo, re.IGNORECASE)
            if match:
                tipo_lei = match.group(1).title()
                numero_lei = match.group(2)
                df.at[idx, 'Nome da Lei'] = f"{tipo_lei} nº {numero_lei}"
            
            if pd.isna(row.get('Jurisdição')):
                df.at[idx, 'Jurisdição'] = 'Brasil'
            
            if not pd.isna(row.get('Url')):
                df.at[idx, 'Material Online (escreva SIM ou deixe em branco)'] = 'SIM'
    
    return df

async def processar_bibliografia_async(df, task_id):
    """Processa toda a planilha buscando ISBNs e identificando tipos"""
    df_resultado = df.copy()
    total = len(df)
    
    stats = {
        'encontrados': 0,
        'nao_encontrados': 0,
        'tipos': {'Livro': 0, 'Capítulo de livro': 0, 'Artigo': 0, 'Trabalho acadêmico': 0, 'Lei': 0}
    }
    
    processing_status[task_id] = {
        'status': 'processing',
        'progress': 0,
        'total': total,
        'message': 'Processando bibliografia...'
    }
    
    for idx, row in df.iterrows():
        titulo = row.get('Título')
        autor = row.get('Autor')
        
        if pd.isna(titulo):
            continue
        
        info_livro = buscar_info_livro(titulo, autor, debug=False)
        
        if info_livro:
            for col, valor in preencher_colunas_por_tipo(row, info_livro).items():
                df_resultado.at[idx, col] = valor
            
            stats['encontrados'] += 1
            tipo = info_livro.get('tipo_citacao', 'Livro')
            stats['tipos'][tipo] = stats['tipos'].get(tipo, 0) + 1
        else:
            stats['nao_encontrados'] += 1
        
        # Atualiza progresso
        progress = int((idx + 1) / total * 100)
        processing_status[task_id]['progress'] = progress
        processing_status[task_id]['message'] = f"Processado {idx + 1} de {total} registros"
        
        # Aguarda para não sobrecarregar a API
        await asyncio.sleep(1.2)
    
    # Processa leis
    df_resultado = processar_leis(df_resultado)
    
    # Salva resultado
    output_filename = f"bibliografia_processada_{task_id}.xlsx"
    output_path = os.path.join(PROCESSED_DIR, output_filename)
    df_resultado.to_excel(output_path, index=False)
    
    # Salva cache
    with open(f"{CACHE_DIR}/cache_buscas.json", "w", encoding="utf-8") as f:
        json.dump(cache_buscas, f, ensure_ascii=False, indent=2)
    
    # Atualiza status final
    processing_status[task_id] = {
        'status': 'completed',
        'progress': 100,
        'total': total,
        'message': 'Processamento concluído!',
        'stats': stats,
        'output_file': output_filename
    }
    
    return output_path

@app.get("/", response_class=HTMLResponse)
async def home():
    """Página inicial com interface de upload"""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Processador de Bibliografia</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                max-width: 800px;
                margin: 0 auto;
                padding: 20px;
                background-color: #f5f5f5;
            }
            .container {
                background-color: white;
                padding: 30px;
                border-radius: 10px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }
            h1,h2,h3 {
                color: #333;
                text-align: center;
            }
            .upload-area {
                border: 2px dashed #ccc;
                border-radius: 10px;
                padding: 30px;
                text-align: center;
                margin: 20px 0;
                background-color: #fafafa;
            }
            .upload-area:hover {
                border-color: #999;
                background-color: #f0f0f0;
            }
            input[type="file"] {
                display: none;
            }
            .upload-btn {
                background-color: #4CAF50;
                color: white;
                padding: 12px 30px;
                border: none;
                border-radius: 5px;
                cursor: pointer;
                font-size: 16px;
            }
            .upload-btn:hover {
                background-color: #45a049;
            }
            .progress-container {
                display: none;
                margin: 20px 0;
            }
            .progress-bar {
                width: 100%;
                height: 30px;
                background-color: #f0f0f0;
                border-radius: 15px;
                overflow: hidden;
            }
            .progress-fill {
                height: 100%;
                background-color: #4CAF50;
                width: 0%;
                transition: width 0.5s ease;
                text-align: center;
                line-height: 30px;
                color: white;
            }
            .status-message {
                text-align: center;
                margin: 10px 0;
                color: #666;
            }
            .download-btn {
                background-color: #2196F3;
                color: white;
                padding: 12px 30px;
                border: none;
                border-radius: 5px;
                cursor: pointer;
                font-size: 16px;
                text-decoration: none;
                display: inline-block;
                margin: 10px;
            }
            .download-btn:hover {
                background-color: #0b7dda;
            }
            .stats {
                background-color: #f9f9f9;
                padding: 20px;
                border-radius: 5px;
                margin: 20px 0;
            }
            .error {
                color: #f44336;
                margin: 10px 0;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📚 Processador de Bibliografia</h1>
            <h3>Sistema criado para buscar ISBN e ajustar a categoria automaticamente no Google Books</h3>
            <h3>Leva em média 2 minutos para cada 100 referências</h3>
            
            <div class="upload-area" onclick="document.getElementById('fileInput').click()">
                <p>📤 Clique para selecionar o arquivo Excel ou arraste aqui</p>
                <p style="color: #999; font-size: 14px;">Arquivo deve conter a planilha "Bibliografia"</p>
                <input type="file" id="fileInput" accept=".xlsx,.xls" onchange="uploadFile(this)">
            </div>
            
            <button class="upload-btn" onclick="document.getElementById('fileInput').click()">
                Selecionar Arquivo
            </button>
            
            <div class="progress-container" id="progressContainer">
                <div class="progress-bar">
                    <div class="progress-fill" id="progressFill">0%</div>
                </div>
                <div class="status-message" id="statusMessage">Iniciando processamento...</div>
            </div>
            
            <div id="results" style="display: none;">
                <h2>✅ Processamento Concluído!</h2>
                <div class="stats" id="statsContainer"></div>
                <a href="#" id="downloadBtn" class="download-btn">📥 Baixar Arquivo Processado</a>
            </div>
            
            <div id="errorContainer"></div>
        </div>
        
        <script>
            let currentTaskId = null;
            
            function uploadFile(input) {
                const file = input.files[0];
                if (!file) return;
                
                const formData = new FormData();
                formData.append('file', file);
                
                document.getElementById('progressContainer').style.display = 'block';
                document.getElementById('results').style.display = 'none';
                document.getElementById('errorContainer').innerHTML = '';
                
                fetch('/upload', {
                    method: 'POST',
                    body: formData
                })
                .then(response => response.json())
                .then(data => {
                    if (data.task_id) {
                        currentTaskId = data.task_id;
                        checkStatus();
                    } else {
                        throw new Error(data.detail || 'Erro ao processar arquivo');
                    }
                })
                .catch(error => {
                    document.getElementById('errorContainer').innerHTML = 
                        '<p class="error">❌ ' + error.message + '</p>';
                    document.getElementById('progressContainer').style.display = 'none';
                });
            }
            
            function checkStatus() {
                if (!currentTaskId) return;
                
                fetch(`/status/${currentTaskId}`)
                .then(response => response.json())
                .then(data => {
                    updateProgress(data.progress);
                    document.getElementById('statusMessage').textContent = data.message;
                    
                    if (data.status === 'completed') {
                        showResults(data);
                    } else if (data.status === 'processing') {
                        setTimeout(checkStatus, 2000);
                    } else if (data.status === 'error') {
                        document.getElementById('errorContainer').innerHTML = 
                            '<p class="error">❌ Erro no processamento</p>';
                        document.getElementById('progressContainer').style.display = 'none';
                    }
                });
            }
            
            function updateProgress(progress) {
                const fill = document.getElementById('progressFill');
                fill.style.width = progress + '%';
                fill.textContent = progress + '%';
            }
            
            function showResults(data) {
                document.getElementById('results').style.display = 'block';
                
                const stats = data.stats;
                const statsHtml = `
                    <h3>📊 Estatísticas</h3>
                    <p>✅ Encontrados: ${stats.encontrados} (${(stats.encontrados/data.total*100).toFixed(1)}%)</p>
                    <p>❌ Não encontrados: ${stats.nao_encontrados}</p>
                    <h4>Distribuição por tipo:</h4>
                    <ul>
                        ${Object.entries(stats.tipos)
                            .filter(([tipo, count]) => count > 0)
                            .map(([tipo, count]) => `<li>${tipo}: ${count}</li>`)
                            .join('')}
                    </ul>
                `;
                
                document.getElementById('statsContainer').innerHTML = statsHtml;
                document.getElementById('downloadBtn').href = `/download/${data.output_file}`;
            }
            
            // Drag and drop
            const uploadArea = document.querySelector('.upload-area');
            
            uploadArea.addEventListener('dragover', (e) => {
                e.preventDefault();
                uploadArea.style.borderColor = '#999';
            });
            
            uploadArea.addEventListener('dragleave', (e) => {
                e.preventDefault();
                uploadArea.style.borderColor = '#ccc';
            });
            
            uploadArea.addEventListener('drop', (e) => {
                e.preventDefault();
                uploadArea.style.borderColor = '#ccc';
                
                const file = e.dataTransfer.files[0];
                if (file && (file.name.endsWith('.xlsx') || file.name.endsWith('.xls'))) {
                    document.getElementById('fileInput').files = e.dataTransfer.files;
                    uploadFile(document.getElementById('fileInput'));
                }
            });
        </script>
    </body>
    </html>
    """

@app.post("/upload")
async def upload_file(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """Endpoint para upload do arquivo Excel"""
    # Validar arquivo
    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(status_code=400, detail="Arquivo deve ser Excel (.xlsx ou .xls)")
    
    # Gerar ID único para a tarefa
    task_id = str(uuid.uuid4())
    
    # Salvar arquivo
    file_path = os.path.join(UPLOAD_DIR, f"{task_id}_{file.filename}")
    with open(file_path, "wb") as f:
        content = await file.read()
        f.write(content)
    
    try:
        # Carregar DataFrame
        df = pd.read_excel(file_path, sheet_name="Bibliografia", dtype=str)
        
        # Iniciar processamento em background
        background_tasks.add_task(processar_bibliografia_async, df, task_id)
        
        return {"task_id": task_id, "message": "Processamento iniciado"}
    
    except Exception as e:
        # Limpar arquivo em caso de erro
        os.remove(file_path)
        raise HTTPException(status_code=400, detail=f"Erro ao ler arquivo: {str(e)}")

@app.get("/status/{task_id}")
async def get_status(task_id: str):
    """Endpoint para verificar status do processamento"""
    if task_id not in processing_status:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada")
    
    return processing_status[task_id]

@app.get("/download/{filename}")
async def download_file(filename: str):
    """Endpoint para download do arquivo processado"""
    file_path = os.path.join(PROCESSED_DIR, filename)
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    
    return FileResponse(
        path=file_path,
        filename=f"bibliografia_processada_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

@app.get("/health")
async def health_check():
    """Endpoint para verificar se a API está funcionando"""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
