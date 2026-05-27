#!/usr/bin/env python3
"""Generate seed HTML for house project in Momentum PWA."""
import json

items = [
    "🧱 Июн 2026 — Завершение фундамента",
    "🧱 Июл 2026 — Гидроизоляция + дренаж",
    "🧱 Авг 2026 — Обратная засыпка",
    "📋 Сен 2026 — Закупка материалов на коробку",
    "🏗 Окт 2026 — Кладка стен 1й этаж (начало)",
    "🏗 Ноя 2026 — Кладка стен 1й этаж (конец)",
    "❄️ Дек 2026 — Зимняя консервация",
    "📋 Янв 2027 — Планирование 2го этажа",
    "🏗 Фев 2027 — Перекрытие 1го этажа",
    "🏗 Мар 2027 — Кладка стен 2й этаж",
    "🏗 Апр 2027 — Завершение стен + армопояс",
    "🏠 Май 2027 — Стропильная система",
    "🏠 Июн 2027 — Кровля",
    "🪟 Июл 2027 — Окна и входная дверь",
    "🧱 Авг 2027 — Утепление фасада",
    "🎨 Сен 2027 — Фасад (начало)",
    "🎨 Окт 2027 — Фасад (конец)",
    "📋 Ноя 2027 — Коробка закрыта ✅",
    "⚡ Дек 2027 — Электрика: разводка",
    "🔧 Янв 2028 — Отопление: котёл + радиаторы",
    "🚿 Фев 2028 — Водоснабжение",
    "🚿 Мар 2028 — Канализация: септик",
    "⚡ Апр 2028 — Щиток + автоматы",
    "🌡 Май 2028 — Вентиляция",
    "📋 Июн 2028 — Коммуникации готовы ✅",
    "💰 Июл 2028 — Финансовый чекпоинт 12М+",
    "🏗 Авг 2028 — Стяжка полов",
    "🏗 Сен 2028 — Штукатурка стен",
    "🏗 Окт 2028 — Потолки",
    "📋 Ноя 2028 — Подготовка к чистовой",
    "🎨 Дек 2028 — Закупка материалов",
    "🚿 Янв 2029 — Ванная + санузел",
    "🍳 Фев 2029 — Кухня",
    "🎨 Мар 2029 — Гостиная",
    "🎨 Апр 2029 — Спальни",
    "🚪 Май 2029 — Двери",
    "⚡ Июн 2029 — Розетки, свет",
    "🎨 Июл 2029 — Лестница",
    "🏠 Авг 2029 — Покраска + обои",
    "📋 Сен 2029 — Чистовая отделка ✅",
    "🛋 Окт 2029 — Мебель: кухня + ванная",
    "🛋 Ноя 2029 — Мебель: спальни + гостиная",
    "🏡 Дек 2029 — Забор + ворота",
    "🌳 Янв 2030 — Ландшафт",
    "📋 Фев 2030 — Пусконаладка систем",
    "📋 Мар 2030 — Регистрация ЕГРН",
    "🧹 Апр 2030 — Генуборка",
    "🎉 Май 2030 — ПЕРЕЕЗД! 🏠🔑",
]

items_json = json.dumps(items, ensure_ascii=False)

html = f'''<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Seed</title></head>
<body style="background:#0d1117;color:#e6edf3;font-family:system-ui;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0">
<div style="background:#161b22;border-radius:16px;padding:40px;text-align:center;max-width:600px;width:90%">
<h1 id="st" style="color:#58a6ff">Loading...</h1>
<p id="nfo"></p>
</div>
<script>
try {{
  var K = "momentum_app_data";
  var raw = localStorage.getItem(K);
  var d = raw ? JSON.parse(raw) : {{projects:[],tasks:[],activities:[],user:{{xp:0,level:1,streakDays:1}}}};
  var items = {items_json};
  var cps = [];
  for (var i = 0; i < items.length; i++) {{
    cps.push({{id:"cp"+i, title:items[i], completed:false}});
  }}
  var filtered = [];
  for (var j = 0; j < d.projects.length; j++) {{
    if (d.projects[j].title.indexOf("\\u0414\\u043e\\u043c") === -1) {{
      filtered.push(d.projects[j]);
    }}
  }}
  d.projects = filtered;
  d.projects.push({{
    id: "house20m",
    title: "\\ud83c\\udfe0 \\u0414\\u043e\\u043c \\u2014 20 \\u043c\\u043b\\u043d \\u20bd",
    description: "\\u0421\\u0442\\u0440\\u043e\\u0438\\u0442\\u0435\\u043b\\u044c\\u0441\\u0442\\u0432\\u043e \\u0434\\u043e\\u043c\\u0430 \\u0437\\u0430 4 \\u0433\\u043e\\u0434\\u0430. \\u0423\\u0447\\u0430\\u0441\\u0442\\u043e\\u043a \\u0435\\u0441\\u0442\\u044c. \\u041f\\u0435\\u0440\\u0435\\u0435\\u0437\\u0434 \\u043a \\u043c\\u0430\\u044e 2030.",
    status: "active",
    progress: 0,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    health: 100,
    boardStatus: "in_progress",
    checkpoints: cps
  }});
  localStorage.setItem(K, JSON.stringify(d));
  document.getElementById("st").textContent = "\\u2705 \\u0413\\u043e\\u0442\\u043e\\u0432\\u043e!";
  document.getElementById("st").style.color = "#3fb950";
  document.getElementById("nfo").innerHTML = cps.length + " checkpoints created.<br><br><a href=\\"./" style=\\"color:#58a6ff;font-size:20px\\">Open Momentum</a>";
}} catch(e) {{
  document.getElementById("st").textContent = "Error: " + e.message;
}}
</script>
</body></html>'''

with open("/tmp/seed2.html", "w", encoding="utf-8") as f:
    f.write(html)
print(f"OK: {len(items)} checkpoints, {len(html)} bytes")
