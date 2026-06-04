import json
from datetime import datetime
from flask import Flask, jsonify, request, render_template  # render_templateを追加

app = Flask(__name__)

# トップページにアクセスしたときにWebアプリの画面を表示する
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/next_bus')
def api_next_bus():
    with open('schedule.json', 'r', encoding='utf-8') as f:
        data = json.load(f)

    direction = request.args.get('dir', 'to_uni')

    now = datetime.now()
    current_time = now.strftime("%H:%M")
    day_type = "weekend" if now.weekday() >= 5 else "weekday"

    next_buses = [bus for bus in data[direction][day_type] if bus["time"] > current_time]

    if next_buses:
        return jsonify({
            "status": "success",
            "current_time": current_time,
            "buses": next_buses[:3]
        })
    else:
        return jsonify({
            "status": "end",
            "current_time": current_time
        })

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)