/**
 * app.js - Main Application Entry
 */
import { store } from './store.js?v=6';
import { Projects } from './projects.js?v=6';
import { Tasks } from './tasks.js?v=6';
import { Coach } from './coach.js?v=6';
import { Motivation } from './motivation.js?v=6';
import { Views } from './views.js?v=6';

const APP_VERSION = '4.0.0';

class App {
  constructor() {
    this.currentRoute = 'dashboard';
  }

  init() {
    console.log("Momentum v" + APP_VERSION + " инициализируется...");

    // Check for stale data and reset if needed
    this.checkDataVersion();

    // Pre-populate demo data if empty
    if (store.data.projects.length === 0) {
      this.populateDemoData();
    }

    // Init modules
    Coach.init();
    Motivation.init();
    Views.init();

    // Set up routing and theme
    this.setupRouting();
    this.setupThemeToggle();
    
    // Initial Render
    this.navigate(this.currentRoute);

    // Global expose for inline event handlers
    window.router = this;
    window.app = this;
    
    // Subscribe to events to trigger re-renders
    store.subscribe('tasks_changed', () => {
      this.renderCurrentView();
      this.updateBadges();
    });
    store.subscribe('projects_changed', () => {
      this.renderCurrentView();
      this.updateBadges();
    });
    
    this.updateBadges();
  }

  checkDataVersion() {
    const storedVersion = localStorage.getItem('momentum_version');
    if (storedVersion !== APP_VERSION) {
      console.log("Обновление данных с версии", storedVersion, "до", APP_VERSION);
      // Clear old data to force regeneration with correct Russian localization
      localStorage.removeItem('momentum_app_data');
      store.data = store.loadData();
      localStorage.setItem('momentum_version', APP_VERSION);
    }
  }

  setupRouting() {
    const navItems = document.querySelectorAll('.nav-item');
    navItems.forEach(item => {
      item.addEventListener('click', (e) => {
        const route = e.currentTarget.getAttribute('data-route');
        if (route) {
          this.navigate(route);
        }
      });
    });
  }

  setupThemeToggle() {
    const btn = document.getElementById('theme-toggle');
    const icon = document.getElementById('theme-icon');
    const root = document.documentElement;
    const metaThemeColor = document.getElementById('theme-color-meta');

    if (!btn || !icon) return;

    // Set initial icon
    if (root.getAttribute('data-theme') === 'light') {
      icon.className = 'ph ph-sun';
    } else {
      icon.className = 'ph ph-moon';
    }

    btn.addEventListener('click', () => {
      const currentTheme = root.getAttribute('data-theme');
      const newTheme = currentTheme === 'light' ? 'dark' : 'light';
      
      root.setAttribute('data-theme', newTheme);
      localStorage.setItem('momentum_theme', newTheme);
      
      if (newTheme === 'light') {
        icon.className = 'ph ph-sun';
        if (metaThemeColor) metaThemeColor.setAttribute('content', '#FAFAFA');
      } else {
        icon.className = 'ph ph-moon';
        if (metaThemeColor) metaThemeColor.setAttribute('content', '#0a0e1a');
      }
    });
  }

  navigate(route) {
    this.currentRoute = route;
    
    // Update sidebar UI
    document.querySelectorAll('.nav-item').forEach(item => {
      item.classList.remove('active');
      if (item.getAttribute('data-route') === route) {
        item.classList.add('active');
      }
    });

    this.renderCurrentView();
  }

  renderCurrentView() {
    switch (this.currentRoute) {
      case 'dashboard': Views.renderDashboard(); break;
      case 'projects': Views.renderProjects(); break;
      case 'tasks': Views.renderTasks(); break;
      case 'timeline': Views.renderTimeline(); break;
      default: Views.renderDashboard();
    }
  }

  updateBadges() {
    const activeProjectsEl = document.getElementById('active-projects-badge');
    const activeCount = store.data.projects.filter(p => p.status === 'active').length;
    if (activeProjectsEl) {
      activeProjectsEl.textContent = activeCount;
      activeProjectsEl.style.display = activeCount > 0 ? 'block' : 'none';
    }
  }

  toggleTask(taskId) {
    Tasks.toggleCompletion(taskId);
    // If we're in project detail view — re-render it
    if (Views._currentProjectId) {
      Views.renderProjectDetail(Views._currentProjectId);
    }
    // Render is also handled by subscription for other views
  }

  toggleCheckpoint(projectId, checkpointId) {
    Projects.toggleCheckpoint(projectId, checkpointId);
  }

  showProjectModal() {
    Views.renderProjectModal();
  }

  closeModal() {
    Views.closeModal();
  }

  // Kanban Drag & Drop Handlers
  onDragStart(e, projectId) {
    e.dataTransfer.setData('text/plain', projectId);
    e.target.classList.add('dragging');
  }

  onDragOver(e) {
    e.preventDefault(); // Necessary to allow dropping
  }

  onDrop(e, newStatus) {
    e.preventDefault();
    const projectId = e.dataTransfer.getData('text/plain');
    if (projectId) {
      Projects.updateBoardStatus(projectId, newStatus);
    }
  }

  onDragEnd(e) {
    e.target.classList.remove('dragging');
  }

  populateDemoData() {
    const now = new Date().toISOString();

    // ══════════════════════════════════════════════
    // MASTER PLAN 1: First $1,000,000 in 3 Years
    // ══════════════════════════════════════════════
    const mp1 = Projects.create({
      title: "💰 $1M за 3 года",
      description: "Мастер-план по заработку первого миллиона долларов. 5 направлений: Алготрейдинг (35%), OneBank (30%), DeepProtect (20%), Крипто-образование (10%), Консалтинг (5%).",
      updatedAt: now,
      boardStatus: 'in_progress',
      checkpoints: [
        { id: "m1", title: "Мес 1 — Фундамент: все системы запущены", completed: false },
        { id: "m2", title: "Мес 2 — Трафик: OneBank 200 юзеров", completed: false },
        { id: "m3", title: "Мес 3 — Оптимизация: $2K/мес трейдинг", completed: false },
        { id: "m6", title: "Мес 6 — $5.5K/мес совокупный доход", completed: false },
        { id: "m9", title: "Мес 9 — $11K/мес, OneBank безубыточность", completed: false },
        { id: "m12", title: "Мес 12 — $17K/мес = $200K run rate", completed: false },
        { id: "y2q2", title: "Год 2 Q2 — $32K/мес", completed: false },
        { id: "y2q4", title: "Год 2 Q4 — $35K/мес", completed: false },
        { id: "y3q2", title: "Год 3 Q2 — $45K/мес", completed: false },
        { id: "y3q4", title: "Год 3 — $1,000,000 накоплено 🎯", completed: false },
      ]
    });

    // Month 1 Week 1-4 tasks
    Tasks.create({ projectId: mp1.id, title: "🤖 Стабилизировать IIE + Pump Hunter: win rate 60%+" });
    Tasks.create({ projectId: mp1.id, title: "🤖 Зафиксировать торговый капитал и лимиты риска" });
    Tasks.create({ projectId: mp1.id, title: "🏦 Подписать франшизу Алтын, оплатить паушальный взнос" });
    Tasks.create({ projectId: mp1.id, title: "🏦 Зарегистрировать TG-бот OneBank, настроить DNS", completed: true });
    Tasks.create({ projectId: mp1.id, title: "🏦 Запустить лендинг onebank.pro", completed: true });
    Tasks.create({ projectId: mp1.id, title: "🏦 Первые 50 тестовых пользователей OneBank" });
    Tasks.create({ projectId: mp1.id, title: "🛡️ MVP DeepProtect SaaS (лендинг + первый сканер)" });
    Tasks.create({ projectId: mp1.id, title: "👥 Нанять маркетолога для OneBank" });

    // Month 2 tasks
    Tasks.create({ projectId: mp1.id, title: "📢 Запустить рекламу OneBank в Telegram (3 креатива)" });
    Tasks.create({ projectId: mp1.id, title: "🤖 IIE: добавить новые монеты в скан" });
    Tasks.create({ projectId: mp1.id, title: "📊 Проанализировать CAC юзеров, оптимизировать воронку" });
    Tasks.create({ projectId: mp1.id, title: "🛡️ DeepProtect: завершить MVP сканера" });
    Tasks.create({ projectId: mp1.id, title: "🔗 Запустить реферальную программу OneBank" });
    Tasks.create({ projectId: mp1.id, title: "📱 Начать вести TG-канал по крипто-трейдингу" });
    Tasks.create({ projectId: mp1.id, title: "🏦 OneBank: 200 регистраций" });

    // Month 3 tasks
    Tasks.create({ projectId: mp1.id, title: "💰 Масштабировать торговый капитал на ботах" });
    Tasks.create({ projectId: mp1.id, title: "🛡️ DeepProtect: запустить бета для 5 клиентов" });
    Tasks.create({ projectId: mp1.id, title: "📢 OneBank: масштабировать рекламу" });
    Tasks.create({ projectId: mp1.id, title: "👥 Нанять SMM-менеджера" });
    Tasks.create({ projectId: mp1.id, title: "📱 TG-канал: 500 подписчиков" });
    Tasks.create({ projectId: mp1.id, title: "📊 Ревью unit-экономики всех направлений" });

    // Q2 tasks (months 4-6)
    Tasks.create({ projectId: mp1.id, title: "💰 Увеличить капитал ботов до $50K" });
    Tasks.create({ projectId: mp1.id, title: "🏦 OneBank: 1500 активных юзеров" });
    Tasks.create({ projectId: mp1.id, title: "🛡️ DeepProtect: 10 платных клиентов × $150/мес" });
    Tasks.create({ projectId: mp1.id, title: "👥 +1 саппорт OneBank, +1 сейлз DeepProtect" });

    // ══════════════════════════════════════════════
    // MASTER PLAN 2: $100M Investment (10 years)
    // ══════════════════════════════════════════════
    const mp2 = Projects.create({
      title: "🏛️ $100M Инвестиции",
      description: "Инвестиционный план на 10 лет совместно с женой. 4 фазы: Фундамент ($0→$100K), Рост ($100K→$1M), Мультипликация ($1M→$10M), Масштаб ($10M→$100M).",
      updatedAt: now,
      boardStatus: 'todo',
      checkpoints: [
        { id: "f1", title: "Фаза 1 — Фундамент: $100K в портфеле", completed: false },
        { id: "f2", title: "Фаза 2 — Рост: $1M совокупный капитал", completed: false },
        { id: "f3", title: "Фаза 3 — Мультипликация: $10M", completed: false },
        { id: "f4", title: "Фаза 4 — Масштаб: $100M 🎯", completed: false },
      ]
    });

    // Phase 1 tasks
    Tasks.create({ projectId: mp2.id, title: "📚 Прочитать 'Разумный инвестор' (Грэм) — вместе с женой" });
    Tasks.create({ projectId: mp2.id, title: "🏦 Открыть брокерский счёт (Interactive Brokers)" });
    Tasks.create({ projectId: mp2.id, title: "₿ Настроить автоматический DCA $500/нед в BTC+ETH" });
    Tasks.create({ projectId: mp2.id, title: "📊 Создать инвестиционный трекер (Google Sheets)" });
    Tasks.create({ projectId: mp2.id, title: "💰 Сформировать подушку безопасности 6 мес расходов" });
    Tasks.create({ projectId: mp2.id, title: "⚖️ Консультация с налоговым юристом" });
    Tasks.create({ projectId: mp2.id, title: "📚 Прочитать 'The Bitcoin Standard'" });
    Tasks.create({ projectId: mp2.id, title: "🏠 Исследовать рынок недвижимости (Таиланд/Бали/Дубай)" });
    Tasks.create({ projectId: mp2.id, title: "📅 Установить ежемесячный семейный совет по инвестициям" });
    Tasks.create({ projectId: mp2.id, title: "🎓 Пройти курс по DeFi на тестнетах" });

    // ══════════════════════════════════════════════
    // Sub-projects for each business direction
    // ══════════════════════════════════════════════
    const ob = Projects.create({
      title: "🏦 OneBank — Запуск",
      description: "Крипто-банк нового поколения. Франшиза Алтын. Карты МИР/VISA, USDT, мгновенные переводы через Telegram Mini App.",
      updatedAt: now,
      boardStatus: 'in_progress',
      checkpoints: [
        { id: "ob1", title: "Франшиза подписана, DNS настроен", completed: false },
        { id: "ob2", title: "MVP запущен, первые 200 юзеров", completed: false },
        { id: "ob3", title: "1 500 юзеров, реферальная программа", completed: false },
        { id: "ob4", title: "Точка безубыточности", completed: false },
      ]
    });

    Tasks.create({ projectId: ob.id, title: "Задеплоить лендинг onebank.pro на VPS", completed: true });
    Tasks.create({ projectId: ob.id, title: "Запустить @OneBankProBot с приветствием", completed: true });
    Tasks.create({ projectId: ob.id, title: "Настроить DNS onebank.pro → VPS" });
    Tasks.create({ projectId: ob.id, title: "Получить SSL-сертификат (Let's Encrypt)" });
    Tasks.create({ projectId: ob.id, title: "Связаться с Алтын по франшизе" });
    Tasks.create({ projectId: ob.id, title: "Подготовить юр. лицо (ИП/ООО)" });
    Tasks.create({ projectId: ob.id, title: "Найти и нанять маркетолога" });

    const dp = Projects.create({
      title: "🛡️ DeepProtect / ZeroChainAI",
      description: "AI-платформа для кибербезопасности. Автоматическое сканирование уязвимостей, аудит смарт-контрактов, отчёты.",
      updatedAt: now,
      boardStatus: 'todo',
      checkpoints: [
        { id: "dp1", title: "MVP сканера готов", completed: false },
        { id: "dp2", title: "5 бета-клиентов", completed: false },
        { id: "dp3", title: "30 платных клиентов, $5K MRR", completed: false },
        { id: "dp4", title: "Enterprise-тариф, API marketplace", completed: false },
      ]
    });

    Tasks.create({ projectId: dp.id, title: "Создать лендинг deepprotect.io" });
    Tasks.create({ projectId: dp.id, title: "Доработать движок сканирования (Threat Model Engine)" });
    Tasks.create({ projectId: dp.id, title: "Интегрировать Smart Audit Engine" });
    Tasks.create({ projectId: dp.id, title: "Запустить бету для 5 клиентов" });
    Tasks.create({ projectId: dp.id, title: "Начать холодные продажи (LinkedIn, email)" });

    const trading = Projects.create({
      title: "🤖 Алготрейдинг — Оптимизация",
      description: "IIE, Pump Hunter, Insider Scanner, Soldier. Цель: стабильные $8K/мес к мес 12.",
      updatedAt: now,
      boardStatus: 'in_progress',
      checkpoints: [
        { id: "t1", title: "Win rate IIE 60%+", completed: false },
        { id: "t2", title: "Стабильные $2K/мес", completed: false },
        { id: "t3", title: "Капитал $50K на ботах", completed: false },
        { id: "t4", title: "Стабильные $8K/мес", completed: false },
        { id: "t5", title: "Запуск сигнального сервиса ($99/мес)", completed: false },
      ]
    });

    Tasks.create({ projectId: trading.id, title: "Стабилизировать IIE v5: автоподстройка параметров" });
    Tasks.create({ projectId: trading.id, title: "Pump Hunter: тест новых монет, фильтр OI>$100M" });
    Tasks.create({ projectId: trading.id, title: "Insider Scanner: ATR-based стопы, снижение leverage до 5x" });
    Tasks.create({ projectId: trading.id, title: "Ежедневный мониторинг через HQ Dashboard" });
    Tasks.create({ projectId: trading.id, title: "Еженедельный P&L отчёт по всем ботам" });

    // ══════════════════════════════════════════════
    // HOUSE PROJECT: 20M RUB, 4 years
    // ══════════════════════════════════════════════
    const house = Projects.create({
      title: "🏠 Дом — 20 млн ₽",
      description: "Строительство дома за 4 года. Участок есть, часть фундамента готова. Переезд к маю 2030.",
      updatedAt: now,
      boardStatus: 'in_progress',
      checkpoints: [
        { id: "h01", title: "🧱 Июн 2026 — Завершение фундамента", completed: false },
        { id: "h02", title: "🧱 Июл 2026 — Гидроизоляция + дренаж", completed: false },
        { id: "h03", title: "🧱 Авг 2026 — Обратная засыпка", completed: false },
        { id: "h04", title: "📋 Сен 2026 — Закупка материалов на коробку", completed: false },
        { id: "h05", title: "🏗 Окт 2026 — Кладка стен 1й этаж (начало)", completed: false },
        { id: "h06", title: "🏗 Ноя 2026 — Кладка стен 1й этаж (конец)", completed: false },
        { id: "h07", title: "❄️ Дек 2026 — Зимняя консервация", completed: false },
        { id: "h08", title: "📋 Янв 2027 — Планирование 2го этажа", completed: false },
        { id: "h09", title: "🏗 Фев 2027 — Перекрытие 1го этажа", completed: false },
        { id: "h10", title: "🏗 Мар 2027 — Кладка стен 2й этаж", completed: false },
        { id: "h11", title: "🏗 Апр 2027 — Завершение стен + армопояс", completed: false },
        { id: "h12", title: "🏠 Май 2027 — Стропильная система", completed: false },
        { id: "h13", title: "🏠 Июн 2027 — Кровля", completed: false },
        { id: "h14", title: "🪟 Июл 2027 — Окна и входная дверь", completed: false },
        { id: "h15", title: "🧱 Авг 2027 — Утепление фасада", completed: false },
        { id: "h16", title: "🎨 Сен 2027 — Фасад (начало)", completed: false },
        { id: "h17", title: "🎨 Окт 2027 — Фасад (конец)", completed: false },
        { id: "h18", title: "📋 Ноя 2027 — Коробка закрыта ✅", completed: false },
        { id: "h19", title: "⚡ Дек 2027 — Электрика: разводка", completed: false },
        { id: "h20", title: "🔧 Янв 2028 — Отопление: котёл + радиаторы", completed: false },
        { id: "h21", title: "🚿 Фев 2028 — Водоснабжение", completed: false },
        { id: "h22", title: "🚿 Мар 2028 — Канализация: септик", completed: false },
        { id: "h23", title: "⚡ Апр 2028 — Щиток + автоматы", completed: false },
        { id: "h24", title: "🌡 Май 2028 — Вентиляция", completed: false },
        { id: "h25", title: "📋 Июн 2028 — Коммуникации готовы ✅", completed: false },
        { id: "h26", title: "💰 Июл 2028 — Финансовый чекпоинт 12М+", completed: false },
        { id: "h27", title: "🏗 Авг 2028 — Стяжка полов", completed: false },
        { id: "h28", title: "🏗 Сен 2028 — Штукатурка стен", completed: false },
        { id: "h29", title: "🏗 Окт 2028 — Потолки", completed: false },
        { id: "h30", title: "📋 Ноя 2028 — Подготовка к чистовой", completed: false },
        { id: "h31", title: "🎨 Дек 2028 — Закупка материалов", completed: false },
        { id: "h32", title: "🚿 Янв 2029 — Ванная + санузел", completed: false },
        { id: "h33", title: "🍳 Фев 2029 — Кухня", completed: false },
        { id: "h34", title: "🎨 Мар 2029 — Гостиная", completed: false },
        { id: "h35", title: "🎨 Апр 2029 — Спальни", completed: false },
        { id: "h36", title: "🚪 Май 2029 — Двери", completed: false },
        { id: "h37", title: "⚡ Июн 2029 — Розетки, свет", completed: false },
        { id: "h38", title: "🎨 Июл 2029 — Лестница", completed: false },
        { id: "h39", title: "🏠 Авг 2029 — Покраска + обои", completed: false },
        { id: "h40", title: "📋 Сен 2029 — Чистовая отделка ✅", completed: false },
        { id: "h41", title: "🛋 Окт 2029 — Мебель: кухня + ванная", completed: false },
        { id: "h42", title: "🛋 Ноя 2029 — Мебель: спальни + гостиная", completed: false },
        { id: "h43", title: "🏡 Дек 2029 — Забор + ворота", completed: false },
        { id: "h44", title: "🌳 Янв 2030 — Ландшафт", completed: false },
        { id: "h45", title: "📋 Фев 2030 — Пусконаладка систем", completed: false },
        { id: "h46", title: "📋 Мар 2030 — Регистрация ЕГРН", completed: false },
        { id: "h47", title: "🧹 Апр 2030 — Генуборка", completed: false },
        { id: "h48", title: "🎉 Май 2030 — ПЕРЕЕЗД! 🏠🔑", completed: false },
      ]
    });
  }
}

// Boot up
document.addEventListener('DOMContentLoaded', () => {
  const momentumApp = new App();
  momentumApp.init();
});
