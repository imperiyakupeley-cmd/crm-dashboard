import os
import streamlit as st
import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
from collections import defaultdict

st.set_page_config(
    page_title="CRM Аналитика — Империя Купелей",
    page_icon="📊",
    layout="wide"
)

WEBHOOK = st.secrets.get("WEBHOOK") or os.environ.get("WEBHOOK")
if not WEBHOOK:
    st.error("WEBHOOK не задан. Добавь его в Settings → Secrets в формате: WEBHOOK = \"https://...\"")
    st.stop()

@st.cache_data(ttl=3600)
def b24(method, params=None):
    r = requests.post(f"{WEBHOOK}/{method}.json", json=params or {}, timeout=15)
    return r.json().get('result', [])

@st.cache_data(ttl=3600)
def get_all(method, params_str):
    params = eval(params_str)
    items, start = [], 0
    while True:
        p = dict(params); p['start'] = start
        result = b24(method, p)
        if isinstance(result, list) and result:
            items.extend(result)
            if len(result) < 50: break
            start += 50
        else: break
    return items

@st.cache_data(ttl=86400)
def load_reference():
    categories = b24('crm.dealcategory.list', {})
    all_stages = {}
    for cid in ['0'] + ([c['ID'] for c in categories] if isinstance(categories, list) else []):
        stages = b24('crm.dealcategory.stage.list', {'id': cid})
        if isinstance(stages, list):
            cat_name = 'Общая' if cid == '0' else next((c['NAME'] for c in (categories or []) if c['ID'] == cid), cid)
            for s in stages:
                all_stages[s['STATUS_ID']] = {'name': s.get('NAME',''), 'category': cat_name, 'sort': int(s.get('SORT',0))}
    sources_raw = b24('crm.status.list', {'filter': {'ENTITY_ID': 'SOURCE'}})
    source_names = {s['STATUS_ID']: s.get('NAME', s['STATUS_ID']) for s in (sources_raw if isinstance(sources_raw, list) else [])}
    return all_stages, source_names

@st.cache_data(ttl=3600)
def load_users():
    users_raw = b24('user.get', {'filter': {'ACTIVE': True}})
    return {u['ID']: f"{u.get('NAME','')} {u.get('LAST_NAME','')}".strip() for u in (users_raw if isinstance(users_raw, list) else [])}

def load_data(date_from, date_to):
    date_from_str = date_from.strftime('%Y-%m-%dT00:00:00')
    date_to_str = date_to.strftime('%Y-%m-%dT23:59:59')
    
    leads = get_all('crm.lead.list', str({
        'filter': {'>=DATE_CREATE': date_from_str, '<=DATE_CREATE': date_to_str},
        'select': ['ID','STATUS_ID','ASSIGNED_BY_ID','SOURCE_ID','DATE_CREATE','OPPORTUNITY']
    }))
    deals = get_all('crm.deal.list', str({
        'filter': {'>=DATE_CREATE': date_from_str, '<=DATE_CREATE': date_to_str},
        'select': ['ID','STAGE_ID','CATEGORY_ID','CLOSED','ASSIGNED_BY_ID','SOURCE_ID','DATE_CREATE','OPPORTUNITY']
    }))
    return leads, deals

# ============ UI ============

st.title("📊 CRM Аналитика — Империя Купелей")

# Фильтры
col1, col2, col3 = st.columns([2, 2, 2])
with col1:
    date_from = st.date_input("С", value=datetime(2026, 4, 1).date())
with col2:
    date_to = st.date_input("По", value=datetime.now().date())
with col3:
    st.write("")
    refresh = st.button("🔄 Обновить данные", use_container_width=True)

if refresh:
    st.cache_data.clear()

with st.spinner("Загружаю данные из Битрикс24..."):
    all_stages, source_names = load_reference()
    users = load_users()
    leads, deals = load_data(date_from, date_to)

def gu(uid): return users.get(str(uid), f"ID:{uid}")

# Обработка лидов
leads_df = pd.DataFrame([{
    'Дата': l.get('DATE_CREATE','')[:10],
    'Менеджер': gu(l.get('ASSIGNED_BY_ID')),
    'Источник': source_names.get(l.get('SOURCE_ID',''), l.get('SOURCE_ID') or 'Не указан'),
    'Статус': l.get('STATUS_ID',''),
    'Конвертирован': l.get('STATUS_ID') == 'CONVERTED',
    'Мусор': l.get('STATUS_ID') == 'JUNK',
} for l in leads]) if leads else pd.DataFrame()

# Обработка сделок
deals_df = pd.DataFrame([{
    'Дата': d.get('DATE_CREATE','')[:10],
    'Менеджер': gu(d.get('ASSIGNED_BY_ID')),
    'Источник': source_names.get(d.get('SOURCE_ID',''), d.get('SOURCE_ID') or 'Не указан'),
    'Воронка': all_stages.get(d.get('STAGE_ID',''), {}).get('category', '?'),
    'Стадия': all_stages.get(d.get('STAGE_ID',''), {}).get('name', d.get('STAGE_ID','')),
    'Закрыта': d.get('CLOSED') == 'Y',
    'Успешна': 'WON' in d.get('STAGE_ID',''),
    'Провалена': 'LOSE' in d.get('STAGE_ID',''),
    'Сумма': float(d.get('OPPORTUNITY') or 0),
} for d in deals]) if deals else pd.DataFrame()

# ============ МЕТРИКИ ============
st.subheader("📈 Ключевые показатели")
m1, m2, m3, m4, m5 = st.columns(5)

total_leads = len(leads_df)
converted = leads_df['Конвертирован'].sum() if not leads_df.empty else 0
conv_rate = round(converted / total_leads * 100, 1) if total_leads else 0
total_deals = len(deals_df)
won = deals_df['Успешна'].sum() if not deals_df.empty else 0

m1.metric("Лидов", total_leads)
m2.metric("Конвертировано", f"{converted} ({conv_rate}%)")
m3.metric("Сделок", total_deals)
m4.metric("Успешных сделок", int(won))
m5.metric("Активных сделок", int((~deals_df['Закрыта']).sum()) if not deals_df.empty else 0)

st.divider()

# ============ ВОРОНКА ЛИДОВ ============
col_left, col_right = st.columns(2)

with col_left:
    st.subheader("🎯 Воронка лидов")
    if not leads_df.empty:
        active = total_leads - converted - int(leads_df['Мусор'].sum())
        funnel_data = pd.DataFrame({
            'Этап': ['Все лиды', 'Активные', 'Конвертировано в сделку'],
            'Количество': [total_leads, active, converted]
        })
        fig = go.Figure(go.Funnel(
            y=funnel_data['Этап'],
            x=funnel_data['Количество'],
            textinfo="value+percent initial",
            marker_color=["#1F4E79", "#2E75B6", "#4CAF50"]
        ))
        fig.update_layout(margin=dict(l=0, r=0, t=20, b=0), height=300)
        st.plotly_chart(fig, use_container_width=True)

with col_right:
    st.subheader("📋 Лиды по источникам")
    if not leads_df.empty:
        src = leads_df.groupby('Источник').size().reset_index(name='Лидов').sort_values('Лидов', ascending=False)
        fig2 = px.bar(src, x='Лидов', y='Источник', orientation='h',
                      color_discrete_sequence=["#2E75B6"])
        fig2.update_layout(margin=dict(l=0, r=0, t=20, b=0), height=300)
        st.plotly_chart(fig2, use_container_width=True)

st.divider()

# ============ СДЕЛКИ ПО СТАДИЯМ ============
st.subheader("📊 Сделки по стадиям")
if not deals_df.empty:
    stages = deals_df.groupby(['Воронка', 'Стадия']).size().reset_index(name='Сделок').sort_values('Сделок', ascending=False)
    fig3 = px.bar(stages, x='Стадия', y='Сделок', color='Воронка',
                  color_discrete_sequence=px.colors.qualitative.Set2)
    fig3.update_layout(margin=dict(l=0, r=0, t=20, b=0), height=350, xaxis_tickangle=-30)
    st.plotly_chart(fig3, use_container_width=True)

st.divider()

# ============ ПО МЕНЕДЖЕРАМ ============
st.subheader("👥 Эффективность менеджеров")
if not leads_df.empty:
    mgr = leads_df.groupby('Менеджер').agg(
        Лидов=('Конвертирован', 'count'),
        Конвертировано=('Конвертирован', 'sum')
    ).reset_index()
    mgr['Конверсия %'] = (mgr['Конвертировано'] / mgr['Лидов'] * 100).round(1)
    
    if not deals_df.empty:
        deal_mgr = deals_df.groupby('Менеджер').agg(
            Сделок=('Успешна', 'count'),
            Успешных=('Успешна', 'sum')
        ).reset_index()
        mgr = mgr.merge(deal_mgr, on='Менеджер', how='left').fillna(0)
    
    mgr = mgr.sort_values('Лидов', ascending=False)
    st.dataframe(mgr, use_container_width=True, hide_index=True)

st.caption(f"Данные обновлены: {datetime.now().strftime('%d.%m.%Y %H:%M')} | Период: {date_from} — {date_to}")
