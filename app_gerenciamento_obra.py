import streamlit as st
import pandas as pd
import plotly.express as px
from supabase import create_client, Client
from datetime import datetime, timedelta

# Configuração da página para formato responsivo (mobile-friendly)
st.set_page_config(page_title="Gestor de Obras", page_icon="🏗️", layout="centered")

# ==========================================
# 1. CONEXÃO COM O SUPABASE
# ==========================================
# (Você precisará colocar suas credenciais no painel do Streamlit: Settings > Secrets)
@st.cache_resource
def init_connection():
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
        return create_client(url, key)
    except:
        return None

supabase = init_connection()

# ==========================================
# 2. FUNÇÕES DE DADOS (SIMULAÇÃO)
# ==========================================
def carregar_cronograma_exemplo():
    # Estrutura base de uma EAP (Estrutura Analítica do Projeto)
    dados = [
        {"Tarefa": "Serviços Preliminares", "Início": "2026-10-05", "Fim": "2026-10-12", "Conclusão (%)": 100, "Fase": "Preparação"},
        {"Tarefa": "Fundação e Infraestrutura", "Início": "2026-10-13", "Fim": "2026-11-10", "Conclusão (%)": 40, "Fase": "Estrutura"},
        {"Tarefa": "Alvenaria e Superestrutura", "Início": "2026-11-11", "Fim": "2026-12-15", "Conclusão (%)": 0, "Fase": "Estrutura"},
        {"Tarefa": "Instalações (Elétrica/Hidro)", "Início": "2026-12-01", "Fim": "2026-12-20", "Conclusão (%)": 0, "Fase": "Instalações"},
        {"Tarefa": "Acabamentos e Revestimentos", "Início": "2026-12-16", "Fim": "2027-01-30", "Conclusão (%)": 0, "Fase": "Acabamento"}
    ]
    df = pd.DataFrame(dados)
    df["Início"] = pd.to_datetime(df["Início"])
    df["Fim"] = pd.to_datetime(df["Fim"])
    return df

# ==========================================
# 3. INTERFACE DE USUÁRIO (UI)
# ==========================================
st.title("🏗️ Planejador de Obras Integrado")

# Criação de abas para navegação no celular
aba1, aba2, aba3 = st.tabs(["📊 Cronograma", "💰 Orçamento (SINAPI)", "⚙️ Configurações"])

with aba1:
    st.subheader("Gráfico de Gantt")
    df_cronograma = carregar_cronograma_exemplo()
    
    # Gerando o gráfico interativo com Plotly
    fig = px.timeline(
        df_cronograma, 
        x_start="Início", 
        x_end="Fim", 
        y="Tarefa", 
        color="Fase",
        hover_data=["Conclusão (%)"],
        title="Linha do Tempo da Obra"
    )
    # Inverte o eixo Y para a primeira tarefa aparecer no topo
    fig.update_yaxes(autorange="reversed") 
    # Ajusta o layout para caber bem em telas menores
    fig.update_layout(margin=dict(l=0, r=0, t=30, b=0), height=400)
    
    st.plotly_chart(fig, use_container_width=True)

    # Tabela editável simples para atualizar percentuais na obra
    st.subheader("Atualizar Avanço Diário")
    df_editado = st.data_editor(df_cronograma[["Tarefa", "Conclusão (%)"]], hide_index=True)

with aba2:
    st.subheader("Consulta de Insumos (Base de Dados)")
    st.write("Conectado ao Supabase para buscar composições de custos em tempo real.")
    
    busca = st.text_input("🔍 Buscar material ou serviço (ex: Porcelanato, Cimento)")
    
    if busca:
        if supabase:
            # Exemplo de consulta real no Supabase quando as tabelas estiverem criadas
            # response = supabase.table('sinapi_insumos').select('*').ilike('descricao', f'%{busca}%').execute()
            # st.dataframe(response.data)
            st.info("A conexão com o Supabase está configurada. Crie a tabela 'sinapi_insumos' no banco para exibir os resultados.")
        else:
            st.warning("Configure as chaves do Supabase nos Secrets do Streamlit para ativar a busca.")
            st.markdown("""
            **Exemplo de resultado simulado:**
            * Código: 37329 | PORCELANATO ESMALTADO | R$ 75,40 / m²
            * Código: 1379 | CIMENTO PORTLAND CP III | R$ 38,90 / SC 50kg
            """)

with aba3:
    st.subheader("Adicionar Nova Tarefa")
    with st.form("nova_tarefa"):
        nome_tarefa = st.text_input("Nome do Serviço")
        fase_tarefa = st.selectbox("Fase", ["Preparação", "Estrutura", "Instalações", "Acabamento"])
        col1, col2 = st.columns(2)
        data_inicio = col1.date_input("Início")
        data_fim = col2.date_input("Término")
        
        submit = st.form_submit_button("Adicionar ao Cronograma")
        if submit:
            st.success(f"Tarefa '{nome_tarefa}' adicionada com sucesso! (Lógica de salvamento a ser implementada no Supabase)")
