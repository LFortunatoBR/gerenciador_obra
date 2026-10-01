import streamlit as st
import pandas as pd
import plotly.express as px
from sqlalchemy import text

# Configuração da página para formato responsivo (mobile-friendly)
st.set_page_config(page_title="Gestor de Obras", page_icon="🏗️", layout="centered")

# ==========================================
# 1. TELA DE LOGIN (BARREIRA DE SEGURANÇA)
# ==========================================
def check_password():
    if "autenticado" not in st.session_state:
        st.session_state["autenticado"] = False

    if not st.session_state["autenticado"]:
        st.title("🔒 Acesso Restrito")
        st.write("Insira a senha para acessar o Gestor de Obras.")
        
        senha_digitada = st.text_input("Senha", type="password")
        if st.button("Entrar"):
            if senha_digitada == st.secrets["senha_app"]:
                st.session_state["autenticado"] = True
                st.rerun() # Recarrega a página autenticado
            else:
                st.error("Senha incorreta!")
        return False
    return True

if not check_password():
    st.stop()

# ==========================================
# 2. CONEXÃO COM O BANCO NEON
# ==========================================
url_correta = st.secrets["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg2://")
conn = st.connection("postgresql", type="sql", url=url_correta)

# ==========================================
# 3. INTERFACE PRINCIPAL
# ==========================================
st.title("🏗️ Planejador de Obras Integrado")

# Abas de navegação
aba1, aba2, aba3 = st.tabs(["📊 Cronograma", "💰 Orçamento (SINAPI)", "⚙️ Gerenciar Tarefas"])

# --- ABA 1: CRONOGRAMA GANTT ---
with aba1:
    st.subheader("Cronograma Físico")
    
    # Busca os dados no banco de dados (ttl=0 garante que os dados estejam sempre atualizados)
    df_tarefas = conn.query("SELECT * FROM tarefas ORDER BY data_inicio;", ttl=0)
    
    if not df_tarefas.empty:
        # Garante que as colunas de data sejam interpretadas corretamente
        df_tarefas['data_inicio'] = pd.to_datetime(df_tarefas['data_inicio'])
        df_tarefas['data_fim'] = pd.to_datetime(df_tarefas['data_fim'])
        
        # Desenha o Gráfico de Gantt
        fig = px.timeline(
            df_tarefas, 
            x_start="data_inicio", 
            x_end="data_fim", 
            y="nome_servico", 
            color="fase",
            hover_data=["conclusao_percentual"],
            title="Evolução da Obra"
        )
        fig.update_yaxes(autorange="reversed") # Coloca a primeira tarefa no topo
        fig.update_layout(height=400, margin=dict(l=0, r=0, t=30, b=0))
        
        st.plotly_chart(fig, use_container_width=True)
        
        # Exibe a tabela de dados logo abaixo
        st.dataframe(df_tarefas[["nome_servico", "fase", "data_inicio", "data_fim", "conclusao_percentual"]], hide_index=True)
    else:
        st.info("Nenhuma tarefa cadastrada. Vá para a aba 'Gerenciar Tarefas' para começar o planejamento.")

# --- ABA 2: ORÇAMENTO SINAPI ---
with aba2:
    st.subheader("Consulta de Custos")
    busca = st.text_input("🔍 Buscar material ou serviço (ex: Cimento, Tijolo)")
    
    if busca:
        # Busca no banco de dados filtrando pelo que foi digitado (ILIKE ignora maiúsculas)
        query = f"SELECT codigo, descricao, unidade, preco_mediano FROM sinapi_insumos WHERE descricao ILIKE '%{busca}%' LIMIT 30;"
        df_sinapi = conn.query(query, ttl=600)
        
        if not df_sinapi.empty:
            st.dataframe(df_sinapi, hide_index=True, use_container_width=True)
        else:
            st.warning("Material não encontrado ou base SINAPI ainda não importada.")

# --- ABA 3: ADICIONAR TAREFAS ---
with aba3:
    st.subheader("Adicionar Nova Etapa da Obra")
    
    # Formulário para envio de dados
    with st.form("form_nova_tarefa"):
        nome = st.text_input("Nome do Serviço (ex: Instalação da Piscina)")
        fase = st.selectbox("Fase da Obra", ["Projetos", "Serviços Preliminares", "Infraestrutura", "Superestrutura", "Instalações", "Acabamento"])
        
        col1, col2 = st.columns(2)
        inicio = col1.date_input("Data de Início")
        fim = col2.date_input("Data de Término")
        conclusao = st.slider("Conclusão Atual (%)", 0, 100, 0)
        
        submit = st.form_submit_button("Salvar no Cronograma")
        
        if submit:
            if inicio > fim:
                st.error("Erro: A data de início não pode ser maior que o término!")
            else:
                # O bloco abaixo insere os dados no banco de forma segura
                with conn.session as s:
                    sql = text("""
                        INSERT INTO tarefas (nome_servico, fase, data_inicio, data_fim, conclusao_percentual) 
                        VALUES (:nome, :fase, :inicio, :fim, :conc)
                    """)
                    s.execute(sql, {"nome": nome, "fase": fase, "inicio": inicio, "fim": fim, "conc": conclusao})
                    s.commit()
                st.success(f"'{nome}' adicionado com sucesso!")
                st.rerun() # Atualiza a tela para o gráfico mostrar a nova tarefa
                
    st.divider()
    if st.button("Fazer Logout (Sair)"):
        st.session_state["autenticado"] = False
        st.rerun()
