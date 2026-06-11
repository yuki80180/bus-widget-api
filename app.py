import sqlite3
from datetime import datetime
from flask import Flask, jsonify, request, render_template

app = Flask(__name__)

# トップページにアクセスしたときにWebアプリの画面を表示する
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/next_bus')
def api_next_bus():
    direction = request.args.get('dir', 'to_uni')

    # 現在時刻と平週末の判定
    now = datetime.now()
    current_time = now.strftime("%H:%M")
    day_type = "weekend" if now.weekday() >= 5 else "weekday"

    # データベースから条件に合うバスを直近3件取得する
    conn = sqlite3.connect('bus.db')
    conn.row_factory = sqlite3.Row  # カラム名でデータにアクセスできるようにする設定
    cursor = conn.cursor()

    # SQL文で「方向」「曜日」「現在時刻より後」の条件でソートして3件抽出
    cursor.execute('''
        SELECT time, line, stop 
        FROM bus_schedule 
        WHERE direction = ? AND day_type = ? AND time > ? 
        ORDER BY time ASC 
        LIMIT 3
    ''', (direction, day_type, current_time))
    
    rows = cursor.fetchall()
    conn.close()

    # データの整形
    next_buses = []
    for row in rows:
        next_buses.append({
            "time": row["time"],
            "line": row["line"],
            "stop": row["stop"]
        })

    if next_buses:
        return jsonify({
            "status": "success",
            "current_time": current_time,
            "buses": next_buses
        })
    else:
        return jsonify({
            "status": "end",
            "current_time": current_time
        })

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)