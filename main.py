import streamlit as st
import trino.dbapi

def get_trino_connection():
    return trino.dbapi.connect(
        host="localhost",
        port=8080,
        user="admin",
        catalog="postgresql",  
        schema="public"
    )

st.set_page_config(layout='wide', page_title="Data Analytics", page_icon=":material/edit:")

# Navigation
main_page = st.Page("app.py", title="Main", icon=":material/dashboard:")
attendance_page = st.Page("Attendance.py", title="Attendance", icon=":material/history:")
tasks_page = st.Page("Tasks.py", title="Tasks", icon=":material/description:")

pg = st.navigation([main_page, attendance_page, tasks_page])
pg.run()