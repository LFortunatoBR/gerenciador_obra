import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from sqlalchemy import text
import unicodedata
from fpdf import FPDF
import os
from datetime import datetime, timedelta
import pytz

def remover_acentos(texto):
    return ''.join(c for c in unicodedata.normalize('NFD', str(texto)) if unicodedata.category(c) != 'Mn')

FASES_DA_OBRA = [
    "1. Serviços Preliminares e Projetos", "2. Canteiro de Obras e Locação",
    "3. Movimento de Terra (Terraplenagem)", "4. Fundações e Contenções",
    "5. Superestrutura (Concreto/Aço/Madeira)", "6. Alvenaria e Paredes de Vedação",
    "7. Coberturas e Impermeabilizações", "8. Esquadrias, Portas e Janelas",
    "9. Instalações Hidrossanitárias e Gás", "10. Instalações Elétricas, Lógicas e SPDA",
    "11. Instalações de Combate a Incêndio", "12. Instalações Especiais e Climatização",
    "13. Revestimentos Internos e Externos", "14. Pisos e Rodapés",
    "15. Forros e Pinturas", "16. Louças, Metais e Acessórios",
    "17. Paisagismo e Urbanização", "18. Limpeza Final e Desmobilização",
    "19. Taxas, Licenças e Administrativo"
]

def add_bus_days(start_date, days):
    if days == 0: return start_date
    current = start_date; added = 0; step = 1 if days > 0 else -1
    while added < abs(days):
        current += timedelta(days=step)
        if current.weekday() < 5: added += 1
    return current

def bus_days_between(start, end):
    days = 0; curr = start
    while curr < end:
        if curr.weekday() < 5: days += 1
        curr += timedelta(days=1)
    return days

def rodar_motor_cpm(conn, obra_id):
    with conn.session as s:
        result = s.execute(text("SELECT * FROM tarefas WHERE obra_id = :oid"), {"oid": obra_id}).mappings().all()
        t_dict = {t['id']: dict(t) for t in result}
        mudou = True; loop = 0
        while mudou and loop < 50: 
            mudou = False; loop += 1
            for t_id, t in t_dict.items():
                if t['dependencia_id']:
                    pred = t_dict.get(t['dependencia_id'])
                    if pred:
                        lag = t['lag_dias'] or 0
                        tipo = t['tipo_dep'] or 'TI'
                        nova_ini = t['data_inicio']
                        if tipo == 'TI': nova_ini = add_bus_days(pred['data_fim'], lag + 1)
                        elif tipo == 'II': nova_ini = add_bus_days(pred['data_inicio'], lag)
                        if nova_ini != t['data_inicio']:
                            duracao = bus_days_between(t['data_inicio'], t['data_fim'])
                            t['data_inicio'], t['data_fim'] = nova_ini, add_bus_days(nova_ini, max(0, duracao))
                            mudou = True
        parents = set(t['parent_id'] for t in t_dict.values() if t['parent_id'])
        for p_id in parents:
            children = [t for t in t_dict.values() if t['parent_id'] == p_id]
            if children and p_id in t_dict:
                min_ini, max_fim = min(c['data_inicio'] for c in children), max(c['data_fim'] for c in children)
                sum_c = sum(c['custo_previsto'] or 0 for c in children)
                total_c = sum_c if sum_c > 0 else len(children)
                sum_perc = sum((c['conclusao_percentual']*(c['custo_previsto'] or 1))/total_c for c in children) if sum_c>0 else sum(c['conclusao_percentual'] for c in children)/len(children)
                if (t_dict[p_id]['data_inicio'] != min_ini or t_dict[p_id]['data_fim'] != max_fim or t_dict[p_id]['custo_previsto'] != sum_c or t_dict[p_id]['conclusao_percentual'] != int(sum_perc)):
                    t_dict[p_id]['data_inicio'], t_dict[p_id]['data_fim'], t_dict[p_id]['custo_previsto'], t_dict[p_id]['conclusao_percentual'] = min_ini, max_fim, float(sum_c), int(sum_perc)
                    mudou = True
        for t_id, t in t_dict.items():
            s.execute(text("UPDATE tarefas SET data_inicio=:i, data_fim=:f, custo_previsto=:c, conclusao_percentual=:p WHERE id=:id"), {"i": t['data_inicio'], "f": t['data_fim'], "c": float(t['custo_previsto'] or 0), "p": int(t['conclusao_percentual'] or 0), "id": t_id})
        s.commit()

st.set_page_config(page_title="ERP Obras", page_icon="🏗", layout="wide")

def check_password():
    if "autenticado" not in st.session_state: st.session_state["autenticado"] = False
    if not st.session_state["autenticado"]:
        st.title("🔒 Acesso Restrito ERP")
        if st.button("Entrar") if st.text_input("Senha", type="password") == st.secrets["senha_app"] else False:
            st.session_state["autenticado"] = True; st.rerun()
        return False
    return True

if not check_password(): st.stop()

url_correta = st.secrets["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg2://")
conn = st.connection("postgresql", type="sql", url=url_correta, pool_pre_ping=True, pool_recycle=300)

fuso_brasil = pytz.timezone('America/Sao_Paulo')
hoje = datetime.now(fuso_brasil).date()

# ==========================================
# BARRA LATERAL (SELEÇÃO DE OBRAS)
# ==========================================
with st.sidebar:
    st.header("🏢 Diretoria & Projetos")
    try: df_obras = conn.query("SELECT * FROM obras ORDER BY id;", ttl=0)
    except: st.error("Rode o SQL no Neon!"); st.stop()
        
    obra_ativa_id = 0
    taxa_bdi = 0.0
    
    obras_dict = {0: "🌐 VISÃO GLOBAL (DASHBOARD)"}
    if not df_obras.empty:
        obras_dict.update(dict(zip(df_obras['id'], df_obras['nome'])))
        
    obra_ativa_id = st.selectbox("Acessar:", options=list(obras_dict.keys()), format_func=lambda x: obras_dict[x])
    
    if obra_ativa_id != 0:
        taxa_bdi = float(df_obras[df_obras['id'] == obra_ativa_id]['bdi'].values[0])
        with st.expander("⚙️ Parametrizar Projeto", expanded=False):
            novo_bdi = st.number_input("Taxa de BDI (%)", min_value=0.0, max_value=100.0, value=taxa_bdi, step=0.1)
            novo_nome = st.text_input("Renomear Projeto:", value=obras_dict[obra_ativa_id])
            if st.button("💾 Salvar Parametros"):
                with conn.session as s:
                    s.execute(text("UPDATE obras SET nome = :n, bdi = :b WHERE id = :id"), {"n": novo_nome, "b": novo_bdi, "id": int(obra_ativa_id)})
                    s.commit()
                st.rerun()
            st.divider()
            if st.button("🗑️ Excluir Projeto"):
                with conn.session as s:
                    s.execute(text("DELETE FROM obras WHERE id = :id"), {"id": int(obra_ativa_id)})
                    s.commit()
                st.rerun()
                
    st.divider()
    with st.form("nova_obra"):
        nova = st.text_input("Novo Projeto")
        if st.form_submit_button("Criar") and nova:
            with conn.session as s:
                s.execute(text("INSERT INTO obras (nome) VALUES (:n)"), {"n": nova}); s.commit()
            st.rerun()

# ==========================================
# DASHBOARD GLOBAL DA DIRETORIA
# ==========================================
if obra_ativa_id == 0:
    st.title("🌐 Dashboard Global da Diretoria")
    st.write("Visão consolidada de todos os projetos ativos da construtora.")
    
    df_all_tarefas = conn.query("SELECT t.obra_id, t.custo_previsto, t.conclusao_percentual, t.parent_id, o.nome, o.bdi FROM tarefas t JOIN obras o ON t.obra_id = o.id WHERE t.parent_id IS NULL;", ttl=0)
    
    if not df_all_tarefas.empty:
        # Agrupa os dados por obra
        resumo = []
        for o_id in df_all_tarefas['obra_id'].unique():
            df_o = df_all_tarefas[df_all_tarefas['obra_id'] == o_id]
            nome_ob = df_o.iloc[0]['nome']
            bdi_ob = float(df_o.iloc[0]['bdi'])
            c_tot = df_o['custo_previsto'].sum()
            v_tot = c_tot * (1 + (bdi_ob/100))
            conc_med = sum(df_o['conclusao_percentual'] * df_o['custo_previsto']) / c_tot if c_tot > 0 else 0
            resumo.append({"Obra": nome_ob, "Custo Total": c_tot, "Preço Venda": v_tot, "Avanço Físico (%)": conc_med})
            
        df_resumo = pd.DataFrame(resumo)
        
        c1, c2, c3 = st.columns(3)
        c1.metric("Carteira Total (VGV)", f"R$ {df_resumo['Preço Venda'].sum():,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        c2.metric("Custo Físico Total", f"R$ {df_resumo['Custo Total'].sum():,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        lucro_bruto = df_resumo['Preço Venda'].sum() - df_resumo['Custo Total'].sum()
        c3.metric("Lucro Bruto Projetado", f"R$ {lucro_bruto:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        
        fig_dash = px.bar(df_resumo, x="Obra", y="Avanço Físico (%)", text="Avanço Físico (%)", title="Avanço Físico por Obra", color="Avanço Físico (%)", color_continuous_scale="RdYlGn", range_y=[0, 100])
        fig_dash.update_traces(texttemplate='%{text:.1f}%', textposition='outside')
        st.plotly_chart(fig_dash, use_container_width=True)
        
        st.dataframe(df_resumo, column_config={"Custo Total": st.column_config.NumberColumn(format="R$ %.2f"), "Preço Venda": st.column_config.NumberColumn(format="R$ %.2f"), "Avanço Físico (%)": st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100)}, hide_index=True, use_container_width=True)
    else:
        st.info("Nenhuma tarefa registada em nenhum projeto ainda.")
    st.stop() # Para a execução aqui. As abas individuais só abrem se selecionar uma obra.

# ==========================================
# GESTÃO DA OBRA SELECIONADA
# ==========================================
st.title(f"🏗️ {obras_dict[obra_ativa_id]}")

df_tarefas = conn.query("SELECT * FROM tarefas WHERE obra_id = :oid ORDER BY data_inicio, id;", params={"oid": int(obra_ativa_id)}, ttl=0)
opcoes_dep, opcoes_parent = {0: "Nenhuma"}, {0: "Nenhuma (É Macro-etapa raiz)"}
if not df_tarefas.empty:
    for _, r in df_tarefas.iterrows():
        n_tarefa = r['nome_servico'] if pd.notna(r['nome_servico']) else f"ID {r['id']}"
        opcoes_dep[r['id']] = n_tarefa
        if pd.isna(r['parent_id']): opcoes_parent[r['id']] = f"📦 {n_tarefa}"

aba1, aba2, aba3, aba4, aba5, aba6, aba7 = st.tabs(["📊 Gantt & EAP", "📈 Curva S (Medição)", "💸 Financeiro", "🛒 Insumos (Curva ABC)", "📖 RDO", "💰 SINAPI", "⚙️ Planejar"])

# --- ABA 1: GANTT, EAP E PDF ---
with aba1:
    col_met1, col_met2, col_btn = st.columns([2, 2, 1])
    df_top_level = df_tarefas[df_tarefas['parent_id'].isna()]
    custo_total = df_top_level['custo_previsto'].sum() if not df_top_level.empty else 0
    preco_venda = custo_total * (1 + (taxa_bdi/100))
    
    col_met1.metric("Custo Total (Interno)", f"R$ {custo_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    col_met2.metric(f"Preço de Venda (BDI {taxa_bdi}%)", f"R$ {preco_venda:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    if col_btn.button("🔄 Recalcular CPM", type="primary", use_container_width=True):
        rodar_motor_cpm(conn, int(obra_ativa_id)); st.rerun()

    if not df_tarefas.empty:
        df_tarefas['data_inicio'] = pd.to_datetime(df_tarefas['data_inicio']).dt.date
        df_tarefas['data_fim'] = pd.to_datetime(df_tarefas['data_fim']).dt.date
        
        df_tarefas['plot_ini'] = pd.to_datetime(df_tarefas['data_inicio'])
        df_tarefas['plot_fim'] = pd.to_datetime(df_tarefas['data_fim'])
        fig = px.timeline(df_tarefas, x_start="plot_ini", x_end="plot_fim", y="nome_servico", color="fase", title="Evolução Lógica")
        fig.update_yaxes(autorange="reversed"); fig.update_layout(height=400, margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig, use_container_width=True)
        
        st.subheader("📋 Estrutura Analítica (EAP)")
        df_exib_rows = []
        fases_presentes = sorted(df_tarefas['fase'].unique(), key=lambda x: FASES_DA_OBRA.index(x) if x in FASES_DA_OBRA else 999)
        
        for fase in fases_presentes:
            df_fase = df_tarefas[df_tarefas['fase'] == fase]
            for _, row in df_fase.iterrows():
                df_exib_rows.append({
                    "id": row['id'], "Serviço": ("  ↳ " if pd.notna(row['parent_id']) else "📦 ") + row['nome_servico'],
                    "Início": pd.to_datetime(row['data_inicio']).strftime('%d/%m/%Y'), "Fim": pd.to_datetime(row['data_fim']).strftime('%d/%m/%Y'),
                    "Conc. %": row['conclusao_percentual'], "Custo (R$)": row['custo_previsto'], "Venda (R$)": float(row['custo_previsto']) * (1 + (taxa_bdi/100))
                })
            t_custo = df_fase[df_fase['parent_id'].isna()]['custo_previsto'].sum()
            df_exib_rows.append({"id": None, "Serviço": f"➤ SUBTOTAL: {fase.upper()}", "Início": "", "Fim": "", "Conc. %": None, "Custo (R$)": t_custo, "Venda (R$)": t_custo * (1 + (taxa_bdi/100))})
            
        df_exib_rows.append({"id": None, "Serviço": "⭐ TOTAL GERAL", "Início": "", "Fim": "", "Conc. %": None, "Custo (R$)": custo_total, "Venda (R$)": preco_venda})
        df_exib = pd.DataFrame(df_exib_rows)
        st.dataframe(df_exib, column_config={"id": None, "Custo (R$)": st.column_config.NumberColumn(format="R$ %.2f"), "Venda (R$)": st.column_config.NumberColumn(format="R$ %.2f")}, hide_index=True, use_container_width=True)

        st.divider()
        c_pdf1, c_pdf2 = st.columns(2)
        tipo_pdf = "interno" if c_pdf1.button("🔒 Gerar PDF Interno (Custos)") else ("cliente" if c_pdf2.button("💼 Gerar PDF Cliente (Venda)") else None)
            
        if tipo_pdf:
            import matplotlib.pyplot as plt
            import matplotlib.dates as mdates
            fig_pdf, ax = plt.subplots(figsize=(10, 4), dpi=150)
            for idx, row in df_tarefas.copy().sort_values(by='data_inicio', ascending=False).iterrows():
                ax.barh(row['nome_servico'], mdates.date2num(row['plot_fim']) - mdates.date2num(row['plot_ini']), left=mdates.date2num(row['plot_ini']), color="#b0bec5", edgecolor='black')
            ax.xaxis_date(); ax.xaxis.set_major_formatter(mdates.DateFormatter('%d/%m/%Y'))
            plt.xticks(rotation=45, ha='right', fontsize=8); plt.yticks(fontsize=8); plt.tight_layout()
            caminho_img = "gantt_temp.png"
            plt.savefig(caminho_img); plt.close(fig_pdf)

            pdf = FPDF(orientation="L", unit="mm", format="A4")
            pdf.add_page(); pdf.set_font("Arial", "B", 16)
            pdf.cell(270, 10, remover_acentos(f"Cronograma Oficial - {obras_dict[obra_ativa_id]}"), ln=True, align="C")
            pdf.set_font("Arial", "", 12)
            titulo_v = f"Custo Estimado: R$ {custo_total:,.2f}" if tipo_pdf == "interno" else f"Preco de Venda: R$ {preco_venda:,.2f}"
            pdf.cell(270, 10, remover_acentos(titulo_v.replace(",", "X").replace(".", ",").replace("X", ".")), ln=True, align="C")
            pdf.image(caminho_img, x=20, w=250); pdf.ln(5)
            
            pdf.set_font("Arial", "B", 8)
            cn = "Custo" if tipo_pdf == "interno" else "Valor"
            for header, w in zip(["Servico", "Inicio", "Termino", "Conc.", cn], [120, 30, 30, 20, 40]):
                pdf.cell(w, 8, header, 1, ln=(header==cn))
            
            pdf.set_font("Arial", "", 8)
            for _, row in df_exib.iterrows():
                if row['id'] is not None:
                    v = row['Custo (R$)'] if tipo_pdf == "interno" else row['Venda (R$)']
                    pdf.cell(120, 8, remover_acentos(str(row['Serviço']))[:70], 1)
                    pdf.cell(30, 8, str(row['Início']), 1); pdf.cell(30, 8, str(row['Fim']), 1); pdf.cell(20, 8, f"{row['Conc. %']}%", 1)
                    pdf.cell(40, 8, f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", "."), 1, ln=True)
            
            pdf.output("relatorio.pdf")
            with open("relatorio.pdf", "rb") as f: st.session_state['pdf_pronto'] = f.read()
            try: os.remove(caminho_img)
            except: pass

        if 'pdf_pronto' in st.session_state:
            st.download_button("⬇️ Baixar PDF", data=st.session_state['pdf_pronto'], file_name="Cronograma.pdf", mime="application/pdf")

# --- ABA 2: CURVA S E MEDIÇÃO ---
with aba2:
    st.header("📈 Medição e Curva S")
    df_base = df_tarefas[df_tarefas['base_inicio'].notna()].copy()
    
    col_curva, col_med = st.columns([2, 1])
    with col_curva:
        if not df_base.empty:
            min_d, max_d = df_base['base_inicio'].min(), df_base['base_fim'].max()
            datas_g = [min_d + timedelta(days=x) for x in range((max_d - min_d).days + 1)]
            pv_acumulado, acc = [], 0
            for d in datas_g:
                c_dia = 0
                if d.weekday() < 5: 
                    for _, r in df_base.iterrows():
                        if r['base_inicio'] <= d <= r['base_fim']:
                            c_dia += (float(r['base_custo']) * (1 + (taxa_bdi/100))) / (bus_days_between(r['base_inicio'], r['base_fim']) + 1)
                acc += c_dia; pv_acumulado.append(acc)
                
            fig_s = go.Figure()
            fig_s.add_trace(go.Scatter(x=datas_g, y=pv_acumulado, mode='lines', name='Planejado (Venda)', line=dict(color='blue', width=4)))
            ev_tot = sum(float(r['base_custo']) * (1 + (taxa_bdi/100)) * (r['conclusao_percentual']/100) for _, r in df_base.iterrows() if pd.notna(r['base_custo']))
            fig_s.add_trace(go.Scatter(x=[hoje], y=[ev_tot], mode='markers', name='Executado (EV)', marker=dict(color='green', size=15, symbol='star')))
            st.plotly_chart(fig_s, use_container_width=True)
        else:
            st.warning("⚠️ Salve a Linha de Base para ativar a Curva S.")
            if st.button("📸 Salvar Baseline (Fotografia do Planejamento)"):
                with conn.session as s:
                    s.execute(text("UPDATE tarefas SET base_inicio=data_inicio, base_fim=data_fim, base_custo=custo_previsto WHERE obra_id=:oid"), {"oid": int(obra_ativa_id)})
                    s.commit()
                st.rerun()

    with col_med:
        st.subheader("🧾 Fechamento Mensal")
        ev_venda = sum(float(r['custo_previsto']) * (1 + (taxa_bdi/100)) * (r['conclusao_percentual']/100) for _, r in df_tarefas.iterrows())
        df_med = conn.query("SELECT * FROM medicoes WHERE obra_id = :oid ORDER BY data_medicao", params={"oid": int(obra_ativa_id)}, ttl=0)
        faturado = df_med['valor_medido'].sum() if not df_med.empty else 0.0
        saldo = ev_venda - faturado
        
        st.metric("Executado (Preço Venda)", f"R$ {ev_venda:,.2f}")
        st.metric("Já Faturado (Recebido)", f"R$ {faturado:,.2f}")
        st.metric("Saldo Liberado para Cobrança", f"R$ {max(0, saldo):,.2f}")
        
        if saldo > 0 and st.button("💰 Faturar Mês Atual"):
            with conn.session as s:
                s.execute(text("INSERT INTO medicoes (obra_id, data_medicao, valor_medido, percentual_obra) VALUES (:o, :d, :v, :p)"), 
                          {"o": int(obra_ativa_id), "d": hoje, "v": float(saldo), "p": (ev_venda/preco_venda)*100 if preco_venda>0 else 0})
                s.commit()
            st.rerun()

# --- ABA 3: FLUXO DE CAIXA (FINANCEIRO) ---
with aba3:
    st.header("💸 Controle de Caixa (Financeiro)")
    c_f1, c_f2 = st.columns([1, 2])
    with c_f1:
        st.subheader("Lançar Título")
        with st.form("form_fin"):
            f_tipo = st.radio("Tipo", ["Despesa", "Receita"])
            f_desc = st.text_input("Descrição (Ex: Cimento, Empreiteiro)")
            f_val = st.number_input("Valor (R$)", min_value=0.0)
            f_venc = st.date_input("Vencimento")
            f_stat = st.selectbox("Status", ["Pendente", "Pago"])
            if st.form_submit_button("Lançar no Caixa"):
                with conn.session as s:
                    s.execute(text("INSERT INTO financeiro (obra_id, tipo, descricao, valor, data_vencimento, status) VALUES (:o, :t, :d, :v, :dt, :s)"),
                              {"o": int(obra_ativa_id), "t": f_tipo, "d": f_desc, "v": f_val, "dt": f_venc, "s": f_stat})
                    s.commit()
                st.rerun()
                
    with c_f2:
        df_fin = conn.query("SELECT id, tipo, descricao, valor, data_vencimento, status FROM financeiro WHERE obra_id = :oid ORDER BY data_vencimento", params={"oid": int(obra_ativa_id)}, ttl=0)
        if not df_fin.empty:
            st.write("Dê duplo clique no Status para alterar para Pago e clique no botão Salvar abaixo.")
            df_fin['data_vencimento'] = pd.to_datetime(df_fin['data_vencimento']).dt.strftime('%d/%m/%Y')
            df_edit_fin = st.data_editor(df_fin, column_config={"id": None, "tipo": st.column_config.TextColumn(disabled=True), "valor": st.column_config.NumberColumn(format="R$ %.2f")}, hide_index=True, use_container_width=True)
            if st.button("💾 Salvar Alterações de Status"):
                with conn.session as s:
                    for _, row in df_edit_fin.iterrows():
                        s.execute(text("UPDATE financeiro SET status=:s WHERE id=:id"), {"s": row['status'], "id": int(row['id'])})
                    s.commit()
                st.rerun()
                
            rec_pago = df_fin[(df_fin['tipo']=='Receita') & (df_fin['status']=='Pago')]['valor'].sum()
            des_pago = df_fin[(df_fin['tipo']=='Despesa') & (df_fin['status']=='Pago')]['valor'].sum()
            st.metric("Saldo Real em Caixa (Recebido - Pago)", f"R$ {rec_pago - des_pago:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

# --- ABA 4: CURVA ABC (INSUMOS) ---
with aba4:
    st.header("🛒 Lista de Compras (Curva ABC)")
    st.write("Cruza as quantidades do seu projeto com a tabela analítica do SINAPI.")
    
    df_abc = conn.query("""
        SELECT a.codigo_insumo as "Código", a.descricao_insumo as "Insumo", a.unidade as "Unid.", 
               SUM(a.quantidade * t.quantidade_sinapi) as "Qtd Necessária",
               a.preco_unitario as "Preço Unit. (R$)",
               SUM(a.quantidade * t.quantidade_sinapi * a.preco_unitario) as "Custo Total (R$)"
        FROM tarefas t
        JOIN sinapi_analitico a ON t.codigo_sinapi = a.codigo_composicao
        WHERE t.obra_id = :oid AND t.codigo_sinapi IS NOT NULL
        GROUP BY a.codigo_insumo, a.descricao_insumo, a.unidade, a.preco_unitario
        ORDER BY "Custo Total (R$)" DESC
    """, params={"oid": int(obra_ativa_id)}, ttl=0)
    
    if not df_abc.empty:
        st.dataframe(df_abc, column_config={"Qtd Necessária": st.column_config.NumberColumn(format="%.2f"), "Preço Unit. (R$)": st.column_config.NumberColumn(format="R$ %.2f"), "Custo Total (R$)": st.column_config.NumberColumn(format="R$ %.2f")}, hide_index=True, use_container_width=True)
    else:
        st.info("💡 A Tabela Analítica do SINAPI ainda não foi carregada no banco ou você não inseriu tarefas pelo SINAPI. Abaixo segue a lista por Macro-etapa:")
        df_compras = df_tarefas[df_tarefas['parent_id'].isna()][['nome_servico', 'data_inicio', 'custo_previsto']].copy()
        df_compras.columns = ['Pacote de Compra', 'Data Limite', 'Verba']
        st.dataframe(df_compras.sort_values(by='Data Limite'), hide_index=True, use_container_width=True)

# --- ABA 5: RDO ---
with aba5:
    st.header("📖 Diário de Obra (RDO)")
    with st.expander("➕ Preencher RDO de Hoje", expanded=True):
        with st.form("form_rdo"):
            d_rdo = st.date_input("Data", value=hoje)
            clima = st.selectbox("Clima", ["Ensolarado", "Nublado", "Chuvoso", "Chuva Impeditiva"])
            efetivo = st.text_area("Efetivo na Obra", height=60)
            obs = st.text_area("Ocorrências", height=80)
            if st.form_submit_button("Salvar RDO"):
                with conn.session as s:
                    s.execute(text("INSERT INTO rdo (obra_id, data_relatorio, clima, efetivo, observacoes) VALUES (:o, :d, :c, :e, :obs)"), {"o": int(obra_ativa_id), "d": d_rdo, "c": clima, "e": efetivo, "obs": obs})
                    s.commit()
                st.rerun()
    df_rdo = conn.query("SELECT * FROM rdo WHERE obra_id = :oid ORDER BY data_relatorio DESC", params={"oid": int(obra_ativa_id)}, ttl=0)
    for _, row in df_rdo.iterrows():
        with st.expander(f"🗓️ {row['data_relatorio']} - {row['clima']}"):
            st.write(f"**Efetivo:** {row['efetivo']}")
            st.write(f"**Obs:** {row['observacoes']}")

# --- ABA 6: SINAPI ---
with aba6:
    st.subheader("Adicionar do SINAPI")
    tipo_busca = st.radio("O que deseja orçar?", ["Serviços Completos (Composições)", "Materiais Isolados (Insumos)"])
    busca = st.text_input("🔍 Buscar (ex: Alvenaria)")
    if busca:
        tabela_alvo = "sinapi_composicoes" if "Serviços" in tipo_busca else "sinapi_insumos"
        df_sinapi = conn.query(f"SELECT codigo, descricao, unidade, preco_mediano FROM {tabela_alvo} WHERE descricao ILIKE '%{busca}%' LIMIT 15;", ttl=600)
        for index, row in df_sinapi.iterrows():
            with st.expander(f"📦 {row['descricao'][:60]}... | R$ {float(row['preco_mediano']):.2f} / {row['unidade']}"):
                with st.form(f"add_sinapi_{index}"):
                    c1, c2 = st.columns(2)
                    qtd = c1.number_input("Quantidade", min_value=0.1, value=1.0)
                    fase_esc = c2.selectbox("Fase", FASES_DA_OBRA)
                    c3, c4 = st.columns(2)
                    d_ini, d_fim = c3.date_input("Início"), c4.date_input("Término")
                    n_abrev = st.text_input("Nome", value=row['descricao'][:50].title())
                    dep_esc = st.selectbox("Depende de?", options=list(opcoes_dep.keys()), format_func=lambda x: opcoes_dep[x])
                    if st.form_submit_button("➕ Adicionar"):
                        if d_ini > d_fim: st.error("Erro nas datas!")
                        else:
                            with conn.session as s:
                                s.execute(text("""INSERT INTO tarefas (nome_servico, fase, data_inicio, data_fim, conclusao_percentual, custo_previsto, dependencia_id, obra_id, codigo_sinapi, quantidade_sinapi) 
                                                  VALUES (:n, :f, :i, :fim, 0, :c, :d, :ob, :cod, :qs)"""),
                                          {"n": n_abrev, "f": fase_esc, "i": d_ini, "fim": d_fim, "c": float(row['preco_mediano'])*qtd, "d": None if dep_esc==0 else dep_esc, "ob": int(obra_ativa_id), "cod": row['codigo'], "qs": qtd})
                                s.commit()
                            st.rerun()

# --- ABA 7: PLANEJAR KITS ---
with aba7:
    st.subheader("⚙️ Planejamento Avançado")
    modo = st.radio("Método:", ["Kits Rápidos", "Tarefa Manual"])
    if modo == "Kits Rápidos":
        kits = {
            "Fundações Rasas (Sapatas/Blocos)": [{"nome": "Escavação", "fase": "3. Movimento de Terra (Terraplenagem)"}, {"nome": "Armação da Fundação", "fase": "4. Fundações e Contenções"}, {"nome": "Concretagem", "fase": "4. Fundações e Contenções"}],
            "Concretagem (Laje/Pilar)": [{"nome": "Fôrmas", "fase": "5. Superestrutura (Concreto/Aço/Madeira)"}, {"nome": "Armação", "fase": "5. Superestrutura (Concreto/Aço/Madeira)"}, {"nome": "Concretagem", "fase": "5. Superestrutura (Concreto/Aço/Madeira)"}],
            "Alvenaria e Acabamento": [{"nome": "Alvenaria", "fase": "6. Alvenaria e Paredes de Vedação"}, {"nome": "Chapisco", "fase": "13. Revestimentos Internos e Externos"}, {"nome": "Reboco", "fase": "13. Revestimentos Internos e Externos"}],
            "Forro de Gesso (Drywall)": [{"nome": "Estruturação", "fase": "15. Forros e Pinturas"}, {"nome": "Emplacamento", "fase": "15. Forros e Pinturas"}, {"nome": "Juntas", "fase": "15. Forros e Pinturas"}],
            "Pintura": [{"nome": "Selador", "fase": "15. Forros e Pinturas"}, {"nome": "Massa", "fase": "15. Forros e Pinturas"}, {"nome": "Acabamento", "fase": "15. Forros e Pinturas"}],
            "Porcelanato": [{"nome": "Contrapiso", "fase": "14. Pisos e Rodapés"}, {"nome": "Assentamento", "fase": "14. Pisos e Rodapés"}, {"nome": "Rejunte", "fase": "14. Pisos e Rodapés"}],
            "Elétrica": [{"nome": "Tubulação", "fase": "10. Instalações Elétricas, Lógicas e SPDA"}, {"nome": "Cabeamento", "fase": "10. Instalações Elétricas, Lógicas e SPDA"}, {"nome": "Tomadas", "fase": "10. Instalações Elétricas, Lógicas e SPDA"}],
            "Hidráulica": [{"nome": "Tubulação", "fase": "9. Instalações Hidrossanitárias e Gás"}, {"nome": "Teste Estanqueidade", "fase": "9. Instalações Hidrossanitárias e Gás"}, {"nome": "Fechamento", "fase": "9. Instalações Hidrossanitárias e Gás"}],
            "Telhado Colonial": [{"nome": "Madeiramento (Tesouras/Terças)", "fase": "7. Coberturas e Impermeabilizações"}, {"nome": "Assentamento de Telhas", "fase": "7. Coberturas e Impermeabilizações"}, {"nome": "Cumeeira e Rufos", "fase": "7. Coberturas e Impermeabilizações"}]
        }
        kit_sel = st.selectbox("Sistema:", list(kits.keys()))
        with st.form("f_kit"):
            dt_ini = st.date_input("Início da 1ª Etapa")
            cols = st.columns(len(kits[kit_sel]))
            d_lst, c_lst = [], []
            for i, etp in enumerate(kits[kit_sel]):
                with cols[i]:
                    st.markdown(f"**{etp['nome']}**")
                    d_lst.append(st.number_input("Dias", 1, 100, 2, key=f"d_{i}"))
                    c_lst.append(st.number_input("Custo R$", 0.0, format="%.2f", key=f"c_{i}"))
            p_m = st.selectbox("Pertence a qual Macro?", options=list(opcoes_parent.keys()), format_func=lambda x: opcoes_parent[x])
            d_m = st.selectbox("A 1ª depende de quem?", options=list(opcoes_dep.keys()), format_func=lambda x: opcoes_dep[x])
            
            if st.form_submit_button("🚀 Gerar Cascata"):
                c_date = dt_ini; c_dep = None if d_m == 0 else d_m
                with conn.session as s:
                    for i, etp in enumerate(kits[kit_sel]):
                        f_calc = add_bus_days(c_date, d_lst[i] - 1)
                        res = s.execute(text("INSERT INTO tarefas (nome_servico, fase, data_inicio, data_fim, conclusao_percentual, custo_previsto, dependencia_id, obra_id, parent_id, tipo_dep) VALUES (:n, :f, :i, :fim, 0, :c, :d, :ob, :pa, 'TI') RETURNING id"),
                                        {"n": etp['nome'], "f": etp['fase'], "i": c_date, "fim": f_calc, "c": c_lst[i], "d": c_dep, "ob": int(obra_ativa_id), "pa": None if p_m==0 else p_m})
                        c_dep = res.scalar(); c_date = add_bus_days(f_calc, 1)
                    s.commit()
                st.rerun()
    else:
        with st.form("f_man"):
            n, f = st.text_input("Nome"), st.selectbox("Fase", FASES_DA_OBRA)
            c1, c2 = st.columns(2)
            p = c1.selectbox("Macro-etapa?", options=list(opcoes_parent.keys()), format_func=lambda x: opcoes_parent[x])
            cst = c2.number_input("Custo R$", min_value=0.0)
            i, fm = c1.date_input("Início"), c2.date_input("Término")
            c3, c4, c5 = st.columns(3)
            d = c3.selectbox("Depende de?", options=list(opcoes_dep.keys()), format_func=lambda x: opcoes_dep[x])
            tp = c4.selectbox("Ligação", ["TI", "II"]); l = c5.number_input("Lag", value=0)
            if st.form_submit_button("Inserir"):
                if i > fm: st.error("Erro nas datas!")
                else:
                    with conn.session as s:
                        s.execute(text("INSERT INTO tarefas (nome_servico, fase, data_inicio, data_fim, conclusao_percentual, custo_previsto, obra_id, parent_id, dependencia_id, tipo_dep, lag_dias) VALUES (:n, :f, :i, :fim, 0, :c, :ob, :pa, :d, :td, :lag)"),
                                  {"n": n, "f": f, "i": i, "fim": fm, "c": cst, "ob": int(obra_ativa_id), "pa": None if p==0 else p, "d": None if d==0 else d, "td": tp[:2], "lag": l})
                        s.commit()
                    st.rerun()
