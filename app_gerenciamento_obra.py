import streamlit as st
import pandas as pd
import plotly.express as px
from sqlalchemy import text
import unicodedata
from fpdf import FPDF
import os
from datetime import datetime, timedelta
import pytz

def remover_acentos(texto):
    return ''.join(c for c in unicodedata.normalize('NFD', str(texto)) if unicodedata.category(c) != 'Mn')

st.set_page_config(page_title="Gestor de Obras", page_icon="🏗️", layout="wide")

# ==========================================
# 1. TELA DE LOGIN
# ==========================================
def check_password():
    if "autenticado" not in st.session_state:
        st.session_state["autenticado"] = False
    if not st.session_state["autenticado"]:
        st.title("🔒 Acesso Restrito")
        senha_digitada = st.text_input("Senha", type="password")
        if st.button("Entrar"):
            if senha_digitada == st.secrets["senha_app"]:
                st.session_state["autenticado"] = True
                st.rerun()
            else:
                st.error("Senha incorreta!")
        return False
    return True

if not check_password():
    st.stop()

# ==========================================
# 2. CONEXÃO NEON E DADOS GERAIS
# ==========================================
url_correta = st.secrets["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg2://")
conn = st.connection("postgresql", type="sql", url=url_correta)

st.title("🏗️ Planejador de Obras Integrado")

aba1, aba2, aba3 = st.tabs(["📊 Cronograma", "💰 Orçamento (SINAPI)", "⚙️ Planejar Etapas e Kits"])

try:
    df_tarefas = conn.query("SELECT * FROM tarefas ORDER BY data_inicio;", ttl=0)
except Exception as e:
    st.error("Erro ao ler banco de dados. Verifique a conexão.")
    st.stop()

opcoes_dep = {0: "Nenhuma (Em paralelo)"}
tarefas_dict = {}
if not df_tarefas.empty:
    for _, r in df_tarefas.iterrows():
        nome_valido = r['nome_servico'] if pd.notna(r['nome_servico']) and r['nome_servico'] != "" else f"Tarefa s/ nome (ID: {r['id']})"
        opcoes_dep[r['id']] = nome_valido
        tarefas_dict[r['id']] = nome_valido

# --- ABA 1: CRONOGRAMA, TABELA E EXPORTAÇÃO PDF ---
with aba1:
    if not df_tarefas.empty:
        custo_total = df_tarefas['custo_previsto'].sum()
        st.metric(label="Custo Total Previsto da Obra", value=f"R$ {custo_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        
        fuso_brasil = pytz.timezone('America/Sao_Paulo')
        hoje = datetime.now(fuso_brasil).date()
        
        df_tarefas['data_inicio'] = pd.to_datetime(df_tarefas['data_inicio']).dt.date
        df_tarefas['data_fim'] = pd.to_datetime(df_tarefas['data_fim']).dt.date
        
        with st.expander("📈 Gerir Avanço e Limpar Tarefas", expanded=True):
            col_sel, col_sld = st.columns(2)
            
            with col_sel:
                id_selecionado = st.selectbox("Selecione um serviço:", options=[k for k in tarefas_dict.keys()], format_func=lambda x: tarefas_dict[x])
            
            tarefa_selecionada = df_tarefas[df_tarefas['id'] == id_selecionado].iloc[0]
            perc_atual = int(tarefa_selecionada['conclusao_percentual'])
            data_inicio_tarefa = tarefa_selecionada['data_inicio']
            data_fim_tarefa = tarefa_selecionada['data_fim']
            
            with col_sld:
                novo_perc = st.slider("Alterar Conclusão (%)", 0, 100, perc_atual, key="slider_perc")
            
            motivo_bloqueio = None
            
            if hoje > data_fim_tarefa and perc_atual < 100:
                st.error(f"🚨 **EM ATRASO:** O prazo encerrou em {data_fim_tarefa.strftime('%d/%m/%Y')}.")
            
            if novo_perc > 0 and hoje < data_inicio_tarefa:
                motivo_bloqueio = f"⛔ **Data Bloqueada:** Início em {data_inicio_tarefa.strftime('%d/%m/%Y')}. Não é possível relatar avanço antes da data."
                
            dependencia_id = tarefa_selecionada['dependencia_id']
            if pd.notna(dependencia_id) and dependencia_id > 0:
                predecessora = df_tarefas[df_tarefas['id'] == dependencia_id]
                if not predecessora.empty:
                    perc_pred = predecessora.iloc[0]['conclusao_percentual']
                    nome_pred = predecessora.iloc[0]['nome_servico']
                    if perc_pred < 100 and novo_perc > 0:
                        motivo_bloqueio = f"⛔ **Dependência Bloqueada:** A etapa '{nome_pred}' precisa estar 100% concluída para iniciar esta (Atual: {perc_pred}%)."
            
            if motivo_bloqueio:
                st.error(motivo_bloqueio)
                
            col_btn_upd, col_btn_del, espaco = st.columns([1, 1, 2])
            with col_btn_upd:
                if st.button("Gravar Alteração", disabled=bool(motivo_bloqueio)):
                    with conn.session as s:
                        s.execute(text("UPDATE tarefas SET conclusao_percentual = :perc WHERE id = :id"), {"perc": novo_perc, "id": int(id_selecionado)})
                        s.commit()
                    st.success("Atualizado!")
                    st.rerun()

            with col_btn_del:
                if st.button("🗑️ Excluir Tarefa", type="primary"):
                    with conn.session as s:
                        s.execute(text("DELETE FROM tarefas WHERE id = :id"), {"id": int(id_selecionado)})
                        s.commit()
                    st.warning("Tarefa eliminada!")
                    st.rerun()
        
        df_tarefas['data_inicio_plot'] = pd.to_datetime(df_tarefas['data_inicio'])
        df_tarefas['data_fim_plot'] = pd.to_datetime(df_tarefas['data_fim'])

        fig = px.timeline(
            df_tarefas, x_start="data_inicio_plot", x_end="data_fim_plot", y="nome_servico", color="fase",
            hover_data=["conclusao_percentual", "custo_previsto"], title="Evolução da Obra"
        )
        fig.update_yaxes(autorange="reversed")
        fig.update_layout(height=400, margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig, use_container_width=True)
        
        df_tarefas['Nome Predecessora'] = df_tarefas['dependencia_id'].map(tarefas_dict).fillna("-")
        df_exibicao = df_tarefas[["nome_servico", "fase", "data_inicio", "data_fim", "Nome Predecessora", "custo_previsto", "conclusao_percentual"]].copy()
        df_exibicao.columns = ["Serviço", "Fase", "Início", "Término", "Depende de", "Custo Previsto", "Conclusão (%)"]
        df_exibicao['Início'] = pd.to_datetime(df_exibicao['Início']).dt.strftime('%d/%m/%Y')
        df_exibicao['Término'] = pd.to_datetime(df_exibicao['Término']).dt.strftime('%d/%m/%Y')
        
        def formatar_moeda(valor):
            try:
                return f"R$ {float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            except:
                return valor

        df_exibicao['Custo Previsto'] = df_exibicao['Custo Previsto'].apply(formatar_moeda)
        st.dataframe(df_exibicao, hide_index=True, use_container_width=True)
            
    else:
        st.info("Nenhuma tarefa cadastrada.")

# --- ABA 2: BUSCADOR SINAPI ---
with aba2:
    st.subheader("Adicionar Serviços do SINAPI")
    tipo_busca = st.radio("O que deseja orçar?", ["Serviços Completos (Composições)", "Materiais Isolados (Insumos)"])
    busca = st.text_input("🔍 Buscar (ex: Alvenaria, Concreto)")
    
    if busca:
        tabela_alvo = "sinapi_composicoes" if "Serviços" in tipo_busca else "sinapi_insumos"
        query = f"SELECT codigo, descricao, unidade, preco_mediano FROM {tabela_alvo} WHERE descricao ILIKE '%{busca}%' LIMIT 15;"
        df_sinapi = conn.query(query, ttl=600)
        
        if not df_sinapi.empty:
            for index, row in df_sinapi.iterrows():
                with st.expander(f"📦 {row['descricao'][:60]}... | R$ {float(row['preco_mediano']):.2f} / {row['unidade']}"):
                    with st.form(f"add_direto_{index}"):
                        col1, col2 = st.columns(2)
                        quantidade = col1.number_input(f"Quantidade ({row['unidade']})", min_value=0.1, value=1.0)
                        fase_esc = col2.selectbox("Fase", ["Projetos", "Infraestrutura", "Superestrutura", "Acabamento"])
                        col3, col4 = st.columns(2)
                        d_ini = col3.date_input("Início")
                        d_fim = col4.date_input("Término")
                        nome_abrev = st.text_input("Nome", value=row['descricao'][:50].title())
                        dep_escolhida = st.selectbox("Depende de?", options=list(opcoes_dep.keys()), format_func=lambda x: opcoes_dep[x])
                        
                        if st.form_submit_button("➕ Adicionar"):
                            if d_ini > d_fim:
                                st.error("Data de início maior que término!")
                            else:
                                val_dep = None if dep_escolhida == 0 else dep_escolhida
                                with conn.session as s:
                                    sql = text("INSERT INTO tarefas (nome_servico, fase, data_inicio, data_fim, conclusao_percentual, custo_previsto, dependencia_id) VALUES (:n, :f, :i, :fim, 0, :c, :d)")
                                    s.execute(sql, {"n": nome_abrev, "f": fase_esc, "i": d_ini, "fim": d_fim, "c": float(row['preco_mediano'])*quantidade, "d": val_dep})
                                    s.commit()
                                st.rerun()
        else:
            st.warning("Nenhum item encontrado.")

# --- ABA 3: PLANEJAR ETAPAS E KITS ---
with aba3:
    modo_insercao = st.radio("Método de Planejamento:", ["Assistente de Engenharia (Kits Integrados)", "Tarefa Manual Avulsa"])
    st.divider()

    if modo_insercao == "Assistente de Engenharia (Kits Integrados)":
        st.subheader("💡 Kits de Engenharia Constutiva")
        st.write("Cria pacotes completos encadeando o Caminho Crítico (CPM) automaticamente.")
        
        # Dicionário da Inteligência Construtiva
        kits = {
            "Concretagem de Estruturas (Laje/Pilar/Viga)": [
                {"nome": "Montagem de Fôrmas e Escoramento", "fase": "Superestrutura"},
                {"nome": "Corte e Armação de Aço (Ferragem)", "fase": "Superestrutura"},
                {"nome": "Lançamento e Adensamento do Concreto", "fase": "Superestrutura"}
            ],
            "Alvenaria de Vedação e Acabamento Úmido": [
                {"nome": "Elevação da Alvenaria", "fase": "Superestrutura"},
                {"nome": "Chapisco", "fase": "Acabamento"},
                {"nome": "Emboço e Reboco", "fase": "Acabamento"}
            ],
            "Execução de Revestimento Cerâmico/Porcelanato": [
                {"nome": "Preparo e Nivelamento da Base (Contrapiso)", "fase": "Acabamento"},
                {"nome": "Assentamento de Porcelanato com Argamassa", "fase": "Acabamento"},
                {"nome": "Rejuntamento e Limpeza", "fase": "Acabamento"}
            ]
        }
        
        kit_selecionado = st.selectbox("Selecione o Sistema:", list(kits.keys()))
        
        with st.form("form_kit_engenharia"):
            st.write(f"**Cadeia gerada para: {kit_selecionado}**")
            data_inicio_macro = st.date_input("Data de Início da 1ª Etapa (Fôrma/Contrapiso, etc.)")
            
            st.write("**Duração de cada etapa (Dias)**")
            col_dias = st.columns(len(kits[kit_selecionado]))
            
            dias_etapas = []
            for i, etapa in enumerate(kits[kit_selecionado]):
                dias = col_dias[i].number_input(f"{i+1}. {etapa['nome'].split(' ')[0]}", min_value=1, value=2, key=f"dias_{i}")
                dias_etapas.append(dias)
                
            dep_macro = st.selectbox("Este bloco inteiro depende de alguma tarefa anterior?", options=list(opcoes_dep.keys()), format_func=lambda x: opcoes_dep[x])
            
            if st.form_submit_button("🚀 Gerar e Encadear Bloco na Obra"):
                data_atual_calculo = data_inicio_macro
                id_dependencia_atual = None if dep_macro == 0 else dep_macro
                
                with conn.session as s:
                    for i, etapa in enumerate(kits[kit_selecionado]):
                        # Calcula a data de fim somando os dias (menos 1, pois o dia inicial conta)
                        data_fim_calculo = data_atual_calculo + timedelta(days=dias_etapas[i] - 1)
                        
                        sql = text("""
                            INSERT INTO tarefas (nome_servico, fase, data_inicio, data_fim, conclusao_percentual, custo_previsto, dependencia_id) 
                            VALUES (:nome, :fase, :inicio, :fim, 0, 0, :dep) RETURNING id
                        """)
                        result = s.execute(sql, {
                            "nome": etapa['nome'], "fase": etapa['fase'], 
                            "inicio": data_atual_calculo, "fim": data_fim_calculo, "dep": id_dependencia_atual
                        })
                        
                        # O ID retornado pelo banco vira a dependência da próxima etapa do loop
                        id_dependencia_atual = result.scalar() 
                        
                        # A próxima etapa começa no dia seguinte ao término desta
                        data_atual_calculo = data_fim_calculo + timedelta(days=1)
                        
                    s.commit()
                st.success(f"Caminho crítico gerado para {kit_selecionado}!")
                st.rerun()

    else:
        st.subheader("Adicionar Tarefa Manual Avulsa")
        with st.form("form_manual"):
            nome = st.text_input("Nome")
            fase = st.selectbox("Fase", ["Projetos", "Infraestrutura", "Superestrutura", "Instalações", "Acabamento"])
            c1, c2 = st.columns(2)
            inicio = c1.date_input("Início")
            fim = c2.date_input("Término")
            custo = st.number_input("Custo Previsto (R$)", min_value=0.0)
            dep = st.selectbox("Depende de?", options=list(opcoes_dep.keys()), format_func=lambda x: opcoes_dep[x])
            
            if st.form_submit_button("Salvar Manual"):
                v_dep = None if dep == 0 else dep
                with conn.session as s:
                    s.execute(text("INSERT INTO tarefas (nome_servico, fase, data_inicio, data_fim, conclusao_percentual, custo_previsto, dependencia_id) VALUES (:n, :f, :i, :fim, 0, :c, :d)"),
                              {"n": nome, "f": fase, "i": inicio, "fim": fim, "c": custo, "d": v_dep})
                    s.commit()
                st.rerun()
