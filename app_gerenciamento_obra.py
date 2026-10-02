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
# 2. CONEXÃO NEON
# ==========================================
url_correta = st.secrets["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg2://")
conn = st.connection("postgresql", type="sql", url=url_correta)

# ==========================================
# 3. BARRA LATERAL (GESTOR DE PROJETOS)
# ==========================================
with st.sidebar:
    st.header("🏢 Seus Projetos")
    
    try:
        df_obras = conn.query("SELECT * FROM obras ORDER BY id;", ttl=0)
    except:
        st.error("A tabela de obras não existe. Rode o SQL no Neon primeiro!")
        st.stop()
        
    obra_ativa_id = None
    nome_obra_ativa = ""
    
    if not df_obras.empty:
        obras_dict = dict(zip(df_obras['id'], df_obras['nome']))
        obra_ativa_id = st.selectbox("Projeto Ativo:", options=list(obras_dict.keys()), format_func=lambda x: obras_dict[x])
        nome_obra_ativa = obras_dict[obra_ativa_id]
    else:
        st.warning("Nenhum projeto encontrado. Crie um abaixo.")
        
    st.divider()
    with st.form("nova_obra"):
        st.subheader("Novo Projeto")
        nova_obra_nome = st.text_input("Nome da Obra")
        if st.form_submit_button("Criar Obra"):
            if nova_obra_nome:
                with conn.session as s:
                    s.execute(text("INSERT INTO obras (nome) VALUES (:n)"), {"n": nova_obra_nome})
                    s.commit()
                st.success("Criado com sucesso!")
                st.rerun()
    
    st.divider()
    if st.button("🚪 Sair do Sistema"):
        st.session_state["autenticado"] = False
        st.rerun()

st.title(f"🏗️ Painel de Gestão: {nome_obra_ativa if nome_obra_ativa else 'Selecione uma Obra'}")

# Bloqueia o uso do app se não houver obra selecionada
if not obra_ativa_id:
    st.info("👈 Crie ou selecione um projeto na barra lateral para começar a orçar e planear.")
    st.stop()

# Busca apenas as tarefas do PROJETO ATIVO
df_tarefas = conn.query("SELECT * FROM tarefas WHERE obra_id = :oid ORDER BY data_inicio;", params={"oid": obra_ativa_id}, ttl=0)

opcoes_dep = {0: "Nenhuma (Em paralelo)"}
tarefas_dict = {}
if not df_tarefas.empty:
    for _, r in df_tarefas.iterrows():
        nome_valido = r['nome_servico'] if pd.notna(r['nome_servico']) and r['nome_servico'] != "" else f"Tarefa ID {r['id']}"
        opcoes_dep[r['id']] = nome_valido
        tarefas_dict[r['id']] = nome_valido

aba1, aba2, aba3 = st.tabs(["📊 Cronograma", "💰 Orçamento (SINAPI)", "⚙️ Planejar Etapas e Kits"])

# --- ABA 1: CRONOGRAMA, TABELA EDITÁVEL E CASCATA ---
with aba1:
    if not df_tarefas.empty:
        custo_total = df_tarefas['custo_previsto'].sum()
        st.metric(label="Custo Total Previsto", value=f"R$ {custo_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        
        fuso_brasil = pytz.timezone('America/Sao_Paulo')
        hoje = datetime.now(fuso_brasil).date()
        
        df_tarefas['data_inicio'] = pd.to_datetime(df_tarefas['data_inicio']).dt.date
        df_tarefas['data_fim'] = pd.to_datetime(df_tarefas['data_fim']).dt.date
        
        # --- PAINEL DE AVANÇO E REPROGRAMAÇÃO (CASCATA) ---
        with st.expander("📈 Gerir Avanço e Reprogramar Imprevistos", expanded=True):
            col_sel, col_perc, col_dias = st.columns([2, 1, 1])
            with col_sel:
                id_sel = st.selectbox("Selecione o serviço:", options=list(tarefas_dict.keys()), format_func=lambda x: tarefas_dict[x])
            
            tarefa_sel = df_tarefas[df_tarefas['id'] == id_sel].iloc[0]
            perc_atual = int(tarefa_sel['conclusao_percentual'])
            
            with col_perc:
                novo_perc = st.slider("Conclusão (%)", 0, 100, perc_atual)
            
            with col_dias:
                # O input que aciona o Efeito Dominó
                dias_ajuste = st.number_input("Atraso / Chuva (Dias)", value=0, help="Ao adicionar dias, o banco empurrará esta etapa e todas que dependem dela.")

            motivo_bloqueio = None
            if hoje > tarefa_sel['data_fim'] and perc_atual < 100:
                st.error(f"🚨 **EM ATRASO:** O prazo encerrou em {tarefa_sel['data_fim'].strftime('%d/%m/%Y')}. Atualize ou adicione dias de atraso.")
            if novo_perc > 0 and hoje < tarefa_sel['data_inicio']:
                motivo_bloqueio = f"⛔ Início previsto para {tarefa_sel['data_inicio'].strftime('%d/%m/%Y')}. Não pode dar % antecipado."
            if pd.notna(tarefa_sel['dependencia_id']) and tarefa_sel['dependencia_id'] > 0:
                pred = df_tarefas[df_tarefas['id'] == tarefa_sel['dependencia_id']]
                if not pred.empty and pred.iloc[0]['conclusao_percentual'] < 100 and novo_perc > 0:
                    motivo_bloqueio = f"⛔ A predecessora '{pred.iloc[0]['nome_servico']}' precisa estar a 100%."
            if motivo_bloqueio:
                st.error(motivo_bloqueio)
                
            col_btn, col_del, _ = st.columns([1, 1, 3])
            with col_btn:
                if st.button("Gravar / Reprogramar", disabled=bool(motivo_bloqueio), type="primary"):
                    with conn.session as s:
                        # Adiciona os dias às datas. O Trigger do banco faz o resto!
                        sql = text("""
                            UPDATE tarefas 
                            SET conclusao_percentual = :p,
                                data_inicio = data_inicio + :d,
                                data_fim = data_fim + :d
                            WHERE id = :id
                        """)
                        s.execute(sql, {"p": novo_perc, "d": dias_ajuste, "id": int(id_sel)})
                        s.commit()
                    st.success("Reprogramação Aplicada!")
                    st.rerun()
            with col_del:
                if st.button("🗑️ Excluir Tarefa"):
                    with conn.session as s:
                        s.execute(text("DELETE FROM tarefas WHERE id = :id"), {"id": int(id_sel)})
                        s.commit()
                    st.rerun()
        
        # --- GRÁFICO ---
        df_tarefas['data_inicio_plot'] = pd.to_datetime(df_tarefas['data_inicio'])
        df_tarefas['data_fim_plot'] = pd.to_datetime(df_tarefas['data_fim'])

        fig = px.timeline(
            df_tarefas, x_start="data_inicio_plot", x_end="data_fim_plot", y="nome_servico", color="fase",
            hover_data=["conclusao_percentual", "custo_previsto"], title="Evolução da Obra"
        )
        fig.update_yaxes(autorange="reversed")
        fig.update_layout(height=400, margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig, use_container_width=True)
        
        # --- TABELA DE CUSTOS EDITÁVEL ---
        st.subheader("💰 Editar Orçamento")
        df_tarefas['Nome Predecessora'] = df_tarefas['dependencia_id'].map(tarefas_dict).fillna("-")
        df_edicao = df_tarefas[["id", "nome_servico", "data_inicio", "data_fim", "Nome Predecessora", "conclusao_percentual", "custo_previsto"]].copy()
        df_edicao['data_inicio'] = pd.to_datetime(df_edicao['data_inicio']).dt.strftime('%d/%m/%Y')
        df_edicao['data_fim'] = pd.to_datetime(df_edicao['data_fim']).dt.strftime('%d/%m/%Y')
        
        df_editado = st.data_editor(
            df_edicao,
            column_config={
                "id": None, 
                "nome_servico": st.column_config.TextColumn("Serviço", disabled=True),
                "data_inicio": st.column_config.TextColumn("Início", disabled=True),
                "data_fim": st.column_config.TextColumn("Término", disabled=True),
                "Nome Predecessora": st.column_config.TextColumn("Depende de", disabled=True),
                "conclusao_percentual": st.column_config.NumberColumn("Conclusão (%)", disabled=True),
                "custo_previsto": st.column_config.NumberColumn("Custo (R$)", min_value=0.0, format="R$ %.2f")
            },
            hide_index=True, use_container_width=True, key="tbl_custos"
        )
        if st.button("💾 Salvar Tabela de Custos"):
            with conn.session as s:
                for idx, row in df_editado.iterrows():
                    s.execute(text("UPDATE tarefas SET custo_previsto = :c WHERE id = :id"), {"c": float(row['custo_previsto']), "id": int(row['id'])})
                s.commit()
            st.rerun()

        # --- EXPORTAR PDF ---
        st.divider()
        if st.button("📄 Gerar Relatório PDF"):
            with st.spinner("Processando..."):
                import matplotlib.pyplot as plt
                import matplotlib.dates as mdates

                fig_pdf, ax = plt.subplots(figsize=(10, 4), dpi=150)
                df_grafico = df_tarefas.copy().sort_values(by='data_inicio', ascending=False)
                cores_fases = {"Projetos": "#90caf9", "Preparação": "#1976d2", "Administrativo": "#eeeeee", "Infraestrutura": "#ffcc80", "Superestrutura": "#ff9800", "Instalações": "#a5d6a7", "Acabamento": "#4caf50"}
                for idx, row in df_grafico.iterrows():
                    cor = cores_fases.get(str(row['fase']), "#9e9e9e")
                    ax.barh(row['nome_servico'], mdates.date2num(row['data_fim_plot']) - mdates.date2num(row['data_inicio_plot']), left=mdates.date2num(row['data_inicio_plot']), color=cor, edgecolor='black', alpha=0.8)
                ax.xaxis_date()
                ax.xaxis.set_major_formatter(mdates.DateFormatter('%d/%m/%Y'))
                plt.xticks(rotation=45, ha='right', fontsize=8)
                plt.yticks(fontsize=8)
                plt.tight_layout()
                caminho_img = "gantt_temp.png"
                plt.savefig(caminho_img)
                plt.close(fig_pdf)

                pdf = FPDF(orientation="L", unit="mm", format="A4")
                pdf.add_page()
                pdf.set_font("Arial", "B", 16)
                pdf.cell(270, 10, remover_acentos(f"Relatorio de Cronograma - {nome_obra_ativa}"), ln=True, align="C")
                pdf.set_font("Arial", "", 12)
                pdf.cell(270, 10, remover_acentos(f"Custo Total Previsto: R$ {custo_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")), ln=True, align="C")
                pdf.image(caminho_img, x=20, w=250)
                pdf.ln(5)
                pdf.set_font("Arial", "B", 8)
                for header, w in zip(["Servico", "Inicio", "Termino", "Dependencia", "Custo", "Conc."], [75, 20, 20, 75, 30, 20]):
                    pdf.cell(w, 8, header, 1, ln=(header=="Conc."))
                
                pdf.set_font("Arial", "", 8)
                for _, row in df_editado.iterrows():
                    pdf.cell(75, 8, remover_acentos(str(row['nome_servico']))[:45], 1)
                    pdf.cell(20, 8, str(row['data_inicio']), 1)
                    pdf.cell(20, 8, str(row['data_fim']), 1)
                    pdf.cell(75, 8, remover_acentos(str(row['Nome Predecessora']))[:45], 1)
                    pdf.cell(30, 8, f"R$ {float(row['custo_previsto']):,.2f}".replace(",", "X").replace(".", ",").replace("X", "."), 1)
                    pdf.cell(20, 8, f"{row['conclusao_percentual']}%", 1, ln=True)
                
                pdf.output("relatorio.pdf")
                with open("relatorio.pdf", "rb") as f:
                    st.session_state['pdf_pronto'] = f.read()
                try: os.remove(caminho_img)
                except: pass

        if 'pdf_pronto' in st.session_state:
            st.download_button("⬇️ Baixar PDF A4", data=st.session_state['pdf_pronto'], file_name=f"Cronograma_{nome_obra_ativa}.pdf", mime="application/pdf")
    else:
        st.info("Nenhuma tarefa neste projeto.")

# --- ABA 2: BUSCADOR SINAPI ---
with aba2:
    st.subheader("Adicionar do SINAPI à Obra Ativa")
    tipo_busca = st.radio("O que deseja orçar?", ["Serviços Completos (Composições)", "Materiais Isolados (Insumos)"])
    busca = st.text_input("🔍 Buscar")
    if busca:
        tabela_alvo = "sinapi_composicoes" if "Serviços" in tipo_busca else "sinapi_insumos"
        df_sinapi = conn.query(f"SELECT codigo, descricao, unidade, preco_mediano FROM {tabela_alvo} WHERE descricao ILIKE '%{busca}%' LIMIT 15;", ttl=600)
        
        if not df_sinapi.empty:
            for index, row in df_sinapi.iterrows():
                with st.expander(f"📦 {row['descricao'][:60]}... | R$ {float(row['preco_mediano']):.2f} / {row['unidade']}"):
                    with st.form(f"add_direto_{index}"):
                        col1, col2 = st.columns(2)
                        qtd = col1.number_input(f"Quantidade", min_value=0.1, value=1.0)
                        fase_esc = col2.selectbox("Fase", ["Projetos", "Infraestrutura", "Superestrutura", "Acabamento"])
                        c3, c4 = st.columns(2)
                        d_ini, d_fim = c3.date_input("Início"), c4.date_input("Término")
                        nome_abrev = st.text_input("Nome", value=row['descricao'][:50].title())
                        dep_escolhida = st.selectbox("Depende de?", options=list(opcoes_dep.keys()), format_func=lambda x: opcoes_dep[x])
                        
                        if st.form_submit_button("➕ Adicionar"):
                            if d_ini > d_fim: st.error("Erro nas datas!")
                            else:
                                with conn.session as s:
                                    s.execute(text("INSERT INTO tarefas (nome_servico, fase, data_inicio, data_fim, conclusao_percentual, custo_previsto, dependencia_id, obra_id) VALUES (:n, :f, :i, :fim, 0, :c, :d, :ob)"),
                                              {"n": nome_abrev, "f": fase_esc, "i": d_ini, "fim": d_fim, "c": float(row['preco_mediano'])*qtd, "d": None if dep_escolhida==0 else dep_escolhida, "ob": obra_ativa_id})
                                    s.commit()
                                st.rerun()

# --- ABA 3: PLANEJAR ETAPAS E KITS ---
with aba3:
    modo = st.radio("Método:", ["Kits de Engenharia (CPM)", "Tarefa Manual Avulsa"])
    if modo == "Kits de Engenharia (CPM)":
        kits = {
            "Concretagem (Laje/Pilar)": [{"nome": "Fôrmas", "fase": "Superestrutura"}, {"nome": "Armação", "fase": "Superestrutura"}, {"nome": "Concretagem", "fase": "Superestrutura"}],
            "Alvenaria e Acabamento": [{"nome": "Alvenaria", "fase": "Superestrutura"}, {"nome": "Chapisco", "fase": "Acabamento"}, {"nome": "Reboco", "fase": "Acabamento"}]
        }
        kit_sel = st.selectbox("Sistema:", list(kits.keys()))
        with st.form("form_kit"):
            dt_ini = st.date_input("Início da 1ª Etapa")
            cols = st.columns(len(kits[kit_sel]))
            dias_lst, custo_lst = [], []
            for i, etp in enumerate(kits[kit_sel]):
                with cols[i]:
                    st.markdown(f"**{etp['nome']}**")
                    dias_lst.append(st.number_input("Dias", 1, 2, key=f"d_{i}"))
                    custo_lst.append(st.number_input("Custo R$", 0.0, format="%.2f", key=f"c_{i}"))
            dep_m = st.selectbox("Dependência macro?", options=list(opcoes_dep.keys()), format_func=lambda x: opcoes_dep[x])
            
            if st.form_submit_button("🚀 Gerar Cascata na Obra Ativa"):
                curr_date = dt_ini
                curr_dep = None if dep_m == 0 else dep_m
                with conn.session as s:
                    for i, etp in enumerate(kits[kit_sel]):
                        fim_calc = curr_date + timedelta(days=dias_lst[i] - 1)
                        res = s.execute(text("INSERT INTO tarefas (nome_servico, fase, data_inicio, data_fim, conclusao_percentual, custo_previsto, dependencia_id, obra_id) VALUES (:n, :f, :i, :fim, 0, :c, :d, :ob) RETURNING id"),
                                        {"n": etp['nome'], "f": etp['fase'], "i": curr_date, "fim": fim_calc, "c": custo_lst[i], "d": curr_dep, "ob": obra_ativa_id})
                        curr_dep = res.scalar()
                        curr_date = fim_calc + timedelta(days=1)
                    s.commit()
                st.rerun()
    else:
        with st.form("f_manual"):
            n, f = st.text_input("Nome"), st.selectbox("Fase", ["Projetos", "Infraestrutura", "Superestrutura", "Instalações", "Acabamento"])
            c1, c2 = st.columns(2)
            i, fm = c1.date_input("Início"), c2.date_input("Término")
            c = st.number_input("Custo", min_value=0.0)
            d = st.selectbox("Depende de?", options=list(opcoes_dep.keys()), format_func=lambda x: opcoes_dep[x])
            if st.form_submit_button("Salvar Manual"):
                with conn.session as s:
                    s.execute(text("INSERT INTO tarefas (nome_servico, fase, data_inicio, data_fim, conclusao_percentual, custo_previsto, dependencia_id, obra_id) VALUES (:n, :f, :i, :fim, 0, :c, :d, :ob)"),
                              {"n": n, "f": f, "i": i, "fim": fm, "c": c, "d": None if d==0 else d, "ob": obra_ativa_id})
                    s.commit()
                st.rerun()
