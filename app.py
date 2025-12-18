import psycopg2
from psycopg2.extras import DictCursor
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from config import db_host_config

app = Flask(__name__)
app.secret_key = 'cleanliness_null_fix_2025'

# Эмуляция ID сотрудников
EMPLOYEE_MAPPING = {'director_boss': 1, 'manager_sergey': 2, 'mechanic_petrovich': 3}

def get_db_connection():
    if 'db_user' not in session: return None
    try:
        return psycopg2.connect(user=session['db_user'], password=session['db_pass'], **db_host_config)
    except: return None

def get_current_role():
    u = session.get('db_user')
    return {'director_boss': 'Директор', 'manager_sergey': 'Менеджер', 'mechanic_petrovich': 'Механик'}.get(u, 'Гость')

@app.context_processor
def global_data():
    conn = get_db_connection()
    choices = {}
    if conn:
        with conn:
            with conn.cursor() as cursor:
                for field in ['marka', 'model', 'color', 'kuzov', 'transmissiya', 'toplivo', 'status']:
                    cursor.execute(f"SELECT DISTINCT {field} FROM automobile WHERE {field} != '' ORDER BY {field}")
                    choices[field] = [row[0] for row in cursor.fetchall()]
    return dict(current_role=get_current_role(), db_user=session.get('db_user'), DB_CHOICES=choices)

@app.route('/', methods=['GET', 'POST'])
def login():
    if 'db_user' in session: return redirect(url_for('dashboard'))
    if request.method == 'POST':
        try:
            conn = psycopg2.connect(user=request.form['username'], password=request.form['password'], **db_host_config)
            conn.close()
            session['db_user'] = request.form['username']
            session['db_pass'] = request.form['password']
            session['id_sotrudnik'] = EMPLOYEE_MAPPING.get(request.form['username'], 1)
            return redirect(url_for('dashboard'))
        except: flash('Ошибка входа', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/dashboard')
def dashboard():
    conn = get_db_connection()
    if not conn: return redirect(url_for('login'))
    data = {}
    role = session['db_user']
    with conn:
        with conn.cursor(cursor_factory=DictCursor) as cursor:
            cursor.execute("SELECT * FROM automobile ORDER BY id_auto")
            data['cars'] = cursor.fetchall()
            
            if role in ['director_boss', 'manager_sergey']:
                cursor.execute("SELECT SUM(stoimost_obschaya) FROM rental_agreement WHERE status_oplaty = 'Paid'")
                data['revenue'] = cursor.fetchone()[0] or 0
                cursor.execute("SELECT COUNT(*) FROM rental_agreement WHERE data_vozvrata_fact IS NULL")
                data['active_rentals_count'] = cursor.fetchone()[0]
                cursor.execute("SELECT * FROM client_physical")
                data['clients_phys'] = cursor.fetchall()
                cursor.execute("SELECT * FROM client_legal")
                data['clients_legal'] = cursor.fetchall()
                cursor.execute("""SELECT r.*, a.marka, a.model, a.gos_nomer, a.tekuschiy_probeg, COALESCE(cp.fio, cl.naimenovanie) as client_name 
                                  FROM rental_agreement r JOIN automobile a ON r.id_auto = a.id_auto 
                                  LEFT JOIN client_physical cp ON r.id_client = cp.id_client 
                                  LEFT JOIN client_legal cl ON r.id_client = cl.id_client 
                                  WHERE r.data_vozvrata_fact IS NULL""")
                data['active_rentals'] = cursor.fetchall()
                cursor.execute("SELECT r.*, a.marka, a.model FROM rental_agreement r JOIN automobile a ON r.id_auto = a.id_auto WHERE r.data_vozvrata_fact IS NOT NULL LIMIT 10")
                data['history'] = cursor.fetchall()

            if role in ['director_boss', 'mechanic_petrovich']:
                cursor.execute("SELECT m.*, a.marka, a.model FROM maintenance m JOIN automobile a ON m.id_auto = a.id_auto ORDER BY m.data_provedeniya DESC LIMIT 10")
                data['maintenance_history'] = cursor.fetchall()
    return render_template('dashboard.html', data=data)

@app.route('/action/<action_type>', methods=['POST'])
def handle_action(action_type):
    conn = get_db_connection()
    f = request.form
    try:
        with conn:
            with conn.cursor() as cur:
                if action_type == 'add_car':
                    cur.execute("INSERT INTO automobile (marka, model, gos_nomer, vin, god_vypuska, color, kuzov, transmissiya, moschnost, toplivo, stoimost_sutki, zalog, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, 'Available')",
                                (f['marka'], f['model'], f['gos_nomer'], f['vin'], f['god_vypuska'], f['color'], f['kuzov'], f['transmissiya'], f['moschnost'], f['toplivo'], f['stoimost_sutki'], f['zalog']))
                
                elif action_type == 'create_rent':
                    # Обработка чистоты: если пустая строка -> None (NULL)
                    chistota_val = f['chistota'].strip() or None
                    
                    # 1. Создаем договор
                    cur.execute("INSERT INTO rental_agreement (data_nachala, data_okonchaniya_plan, stoimost_obschaya, id_client, id_auto, id_sotrudnik) VALUES (%s,%s,%s,%s,%s,%s) RETURNING nomer_dogovora", 
                                (f['start'], f['end'], f['price'], f['client'], f['auto'], session.get('id_sotrudnik', 1)))
                    dogovor_id = cur.fetchone()[0]
                    
                    # 2. Создаем Акт Выдачи (Issue)
                    cur.execute("INSERT INTO inspection_act (tip_acta, probeg_fix, toplivo_uroven, chistota, nomer_dogovora) VALUES ('Issue', %s, %s, %s, %s)",
                                (f['probeg'], f['toplivo_level'], chistota_val, dogovor_id))
                    
                    # 3. Обновляем авто
                    cur.execute("UPDATE automobile SET status = 'Rented', tekuschiy_probeg = %s WHERE id_auto = %s", (f['probeg'], f['auto']))
                
                elif action_type == 'close_rent':
                    # Обработка чистоты и повреждений: если пустая строка -> None (NULL)
                    chistota_val = f['chistota'].strip() or None
                    povrezhdeniya_val = f['povrezhdeniya'].strip() or None

                    # 1. Закрываем договор
                    cur.execute("UPDATE rental_agreement SET data_vozvrata_fact = CURRENT_DATE, status_oplaty = 'Paid' WHERE nomer_dogovora = %s RETURNING id_auto", (f['id_dogovor'],))
                    aid = cur.fetchone()[0]
                    
                    # 2. Создаем Акт Возврата (Return)
                    cur.execute("INSERT INTO inspection_act (tip_acta, probeg_fix, toplivo_uroven, chistota, opisanie_povrezhdeniy, nomer_dogovora) VALUES ('Return', %s, %s, %s, %s, %s)",
                                (f['probeg'], f['toplivo_level'], chistota_val, povrezhdeniya_val, f['id_dogovor']))
                    
                    # 3. Освобождаем авто
                    cur.execute("UPDATE automobile SET status = 'Available', tekuschiy_probeg = %s WHERE id_auto = %s", (f['probeg'], aid))

                #elif action_type == 'add_client_phys':
                #    cur.execute("INSERT INTO client (tip_clienta) VALUES ('Physical') RETURNING id_client")
                #    cid = cur.fetchone()[0]
                #    cur.execute("INSERT INTO client_physical (id_client, fio, telefon, pasport, adres_reg) VALUES (%s,%s,%s,%s,%s)", (cid, f['fio'], f['telefon'], f['pasport'], f['adres']))
                elif action_type == 'add_client_phys':
                # 1. Создаем ID в общей таблице (Исправили 'Физическое' на 'Physical', как договаривались)
                    cur.execute("INSERT INTO client (tip_clienta) VALUES ('Physical') RETURNING id_client")
                    cid = cur.fetchone()[0]
                    # 2. Записываем данные физлица
                    # Добавили поля: data_rozhdeniya и vu_dannye
                    # Если ВУ не ввели в форме, подставим 'Нет данных', чтобы база не ругалась
                    vu_value = f.get('vu') or 'Нет данных'
                    cur.execute("""INSERT INTO client_physical (id_client, fio, data_rozhdeniya, telefon, pasport, adres_reg, vu_dannye) VALUES (%s, %s, %s, %s, %s, %s, %s)""", (cid, f['fio'], f['data_rozhdeniya'], f['telefon'], f['pasport'], f['adres'], vu_value))
                #elif action_type == 'add_client_legal':
                #    cur.execute("INSERT INTO client (tip_clienta) VALUES ('Legal') RETURNING id_client")
                #    cid = cur.fetchone()[0]
                #    cur.execute("INSERT INTO client_legal (id_client, naimenovanie, inn_kpp, yur_adres, predstavitel) VALUES (%s,%s,%s,%s,%s)", (cid, f['naimenovanie'], f['inn_kpp'], f['yur_adres'], f['predstavitel']))
                elif action_type == 'add_client_legal':
                # 1. Создаем ID (Исправили 'Юридическое' на 'Legal')
                    cur.execute("INSERT INTO client (tip_clienta) VALUES ('Legal') RETURNING id_client")
                    cid = cur.fetchone()[0]
                    # 2. Записываем данные юрлица
                    # Добавили колонку bank_rekvizity и значение f['bank_rekvizity']
                    cur.execute("""INSERT INTO client_legal (id_client, naimenovanie, inn_kpp, yur_adres, bank_rekvizity, predstavitel) VALUES (%s, %s, %s, %s, %s, %s)""", (cid, f['naimenovanie'], f['inn_kpp'], f['yur_adres'], f['bank_rekvizity'], f['predstavitel']))
                elif action_type == 'add_maintenance':
                    cur.execute("INSERT INTO maintenance (data_provedeniya, tip_rabot, probeg_moment, id_auto) VALUES (%s,%s,%s,%s)", (f['date'], f['work'], f['mileage'], f['auto']))
                    cur.execute("UPDATE automobile SET status = 'Maintenance', tekuschiy_probeg = %s WHERE id_auto = %s", (f['mileage'], f['auto']))
        flash('Успешно!', 'success')
    except Exception as e: flash(f'Ошибка: {e}', 'danger')
    return redirect(url_for('dashboard'))

@app.route('/car/edit/<int:id>', methods=['POST'])
def edit_car(id):
    conn = get_db_connection()
    f = request.form
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""UPDATE automobile SET marka=%s, model=%s, gos_nomer=%s, vin=%s, god_vypuska=%s, color=%s, kuzov=%s, transmissiya=%s, moschnost=%s, toplivo=%s, stoimost_sutki=%s, zalog=%s, status=%s WHERE id_auto=%s""",
                            (f['marka'], f['model'], f['gos_nomer'], f['vin'], f['god_vypuska'], f['color'], f['kuzov'], f['transmissiya'], f['moschnost'], f['toplivo'], f['stoimost_sutki'], f['zalog'], f['status'], id))
        flash('Обновлено!', 'success')
    except Exception as e: flash(f'Ошибка: {e}', 'danger')
    return redirect(url_for('dashboard'))

@app.route('/car/delete/<int:id>', methods=['POST'])
def delete_car(id):
    conn = get_db_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM automobile WHERE id_auto = %s", (id,))
        flash('Удалено.', 'warning')
    except Exception as e: flash(f'Ошибка удаления: {e}', 'danger')
    return redirect(url_for('dashboard'))

# --- ПОИСК ЧЕРЕЗ AJAX (БЕЗ ПЕРЕЗАГРУЗКИ) ---
# --- ПОИСК (ИСПРАВЛЕННЫЙ) ---
@app.route('/search', methods=['POST'])
def search_car():
    conn = get_db_connection()
    # ИСПРАВЛЕНИЕ: Берем данные из формы, а не из JSON
    query = request.form.get('search_query', '') 
    
    results = []
    try:
        with conn:
            with conn.cursor(cursor_factory=DictCursor) as cur:
                cur.execute("SELECT * FROM search_automobile_by_plate(%s)", (query,))
                results = cur.fetchall()
    except Exception as e: 
        flash(f'Ошибка поиска: {e}', 'danger')
    
    # Сохраняем результаты в сессию, чтобы показать их после перезагрузки
    session['search_results'] = [dict(row) for row in results]
    session['search_query'] = query
    
    return redirect(url_for('dashboard'))

@app.route('/clear_search')
def clear_search():
    session.pop('search_results', None)
    session.pop('search_query', None)
    return redirect(url_for('dashboard'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)