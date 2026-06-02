import { useEffect, useState } from 'react';

// API base URL - read from environment or fallback to localhost
const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000/api';

interface Position {
  id: number;
  symbol: string;
  direction: 'UP' | 'DOWN';
  ref_price: number;
  current_price: number | null;
  diff_pct: number | null;
  is_winning: boolean | null;
  created_at: string;
  last_warning_distance: number;
}

interface Settings {
  warning_zone_pct: number;
  step_pct: number;
}

const PREDEFINED_MARKETS = [
  { name: 'S&P 500 (SPX) Up/Down', symbol: 'SPY', bet_type: 'close' },
  { name: 'WTI Crude Oil (WTI) Up/Down', symbol: 'WTI', bet_type: 'close' },
  { name: 'SPY (SPY) Up/Down', symbol: 'SPY', bet_type: 'close' },
  { name: 'S&P 500 (SPX) Opens Up/Down', symbol: 'SPY', bet_type: 'open' },
  { name: 'Gold (XAUUSD) Up/Down', symbol: 'XAU', bet_type: 'close' },
  { name: 'Palantir (PLTR) Up/Down', symbol: 'PLTR', bet_type: 'close' },
  { name: 'Amazon (AMZN) Up/Down', symbol: 'AMZN', bet_type: 'close' },
  { name: 'NVIDIA (NVDA) Up/Down', symbol: 'NVDA', bet_type: 'close' },
  { name: 'Robinhood (HOOD) Up/Down', symbol: 'HOOD', bet_type: 'close' },
  { name: 'Apple (AAPL) Up/Down', symbol: 'AAPL', bet_type: 'close' },
  { name: 'Meta (META) Up/Down', symbol: 'META', bet_type: 'close' },
  { name: 'Google (GOOGL) Up/Down', symbol: 'GOOGL', bet_type: 'close' },
  { name: 'Tesla (TSLA) Up/Down', symbol: 'TSLA', bet_type: 'close' },
  { name: 'Natural Gas (NG) Up/Down', symbol: 'NG', bet_type: 'close' },
  { name: 'Russell 2000 (RUT) Up/Down', symbol: 'RUT', bet_type: 'close' },
  { name: 'Airbnb (ABNB) Up/Down', symbol: 'ABNB', bet_type: 'close' },
  { name: 'Opendoor (OPEN) Up/Down', symbol: 'OPEN', bet_type: 'close' },
  { name: 'Microsoft (MSFT) Up/Down', symbol: 'MSFT', bet_type: 'close' },
  { name: 'Coinbase (COIN) Up/Down', symbol: 'COIN', bet_type: 'close' },
  { name: 'EWY (EWY) Up/Down', symbol: 'EWY', bet_type: 'close' },
  { name: 'Silver (XAGUSD) Up/Down', symbol: 'XAG', bet_type: 'close' },
  { name: 'Netflix (NFLX) Up/Down', symbol: 'NFLX', bet_type: 'close' },
  { name: 'Rocket Lab (RKLB) Up/Down', symbol: 'RKLB', bet_type: 'close' },
  { name: 'Hang Seng (HSI) Up/Down', symbol: 'HSI', bet_type: 'close' },
  { name: 'Dow Jones (DJIA) Up/Down', symbol: 'DIA', bet_type: 'close' },
  { name: 'DAX (DAX) Up/Down', symbol: 'DAX', bet_type: 'close' },
  { name: 'Nikkei 225 (NIK) Up/Down', symbol: 'NKY', bet_type: 'close' },
  { name: 'FTSE 100 (UKX) Up/Down', symbol: 'UKX', bet_type: 'close' },
  { name: 'NYA (NYA) Up/Down', symbol: 'NYA', bet_type: 'close' }
];

interface ScanResult {
  symbol: string;
  direction: 'UP' | 'DOWN';
  current_price: number;
  ref_price: number;
  diff_pct: number;
  minutes_to_close: number;
  is_off_hours: boolean;
  off_hours_reason: string;
  historical: {
    total_similar_days: number;
    reversed_count: number;
    reversal_rate: number;
    worst_case: number;
    confidence_stars: string;
    confidence_label: string;
  };
  poly: {
    slug: string;
    up_price: number;
    down_price: number;
    safe_outcome_price: number;
    best_ask: number | null;
    depth_at_best: number;
    depth_at_99: number;
    has_orders_at_99: boolean;
  };
  is_safe_bet: boolean;
  is_impossible: boolean;
  has_orders_at_99: boolean;
}

const TRANSLATIONS = {
  tr: {
    title: "Poly Up/Down Tracker",
    subtitle: "Gerçek zamanlı pazar takip ve uyarı sistemi",
    stocksClose: "Hisseler Kapanış: 23:00 TR",
    commoditiesClose: "Emtialar Kapanış: 24:00 TR",
    tabPositions: "📈 Pozisyon Takip ve Alarm",
    tabScanner: "🎯 Finance Signal Scanner",
    tabTrades: "🤖 Robot Yorumları & Hasatlar",
    newTag: "Yeni",
    activePositions: "Aktif Pozisyonlar",
    noActivePositions: "Henüz aktif pozisyon yok. Telegram'dan /up SPX komutu ile pozisyon ekleyebilirsiniz.",
    refPriceLabel: "Referans (Dünkü Kapanış)",
    livePythPrice: "Anlık Pyth Fiyatı",
    statusLabel: "Durum",
    winning: "KAZANIYOR",
    losing: "KAYBEDİYOR",
    quickAddTitle: "Polymarket Hızlı Ekleme",
    addAlertTitle: "Yeni Alarm Ekle",
    symbolLabel: "Sembol (Örn: SPX, PLTR)",
    typeLabel: "Tür",
    directionLabel: "Yön",
    dailyClose: "Günlük (Close)",
    openingOpen: "Açılış (Open)",
    up: "Yukarı (UP)",
    down: "Aşağı (DOWN)",
    addAlertButton: "Alarm Ekle",
    addingButton: "Ekleniyor...",
    alertAdded: "Pozisyon eklendi ve Telegram'a bildirildi!",
    tgSettingsTitle: "Telegram Uyarı Ayarları",
    dangerZoneLabel: "Tehlike Bölgesi (%)",
    dangerZoneDesc: "Fiyat referans çizgisine bu kadar yaklaştığında uyarılar başlar.",
    stepLabel: "Uyarı Adımı (%)",
    stepDesc: "Tehlike bölgesindeyken her bu kadarlık harekette yeni mesaj atılır.",
    saveSettings: "Ayarları Kaydet",
    settingsSaved: "Ayarlar kaydedildi!",
    settingsDesc: "Örnek: Tehlike Bölgesi %1, Adım %0.1 ise; Fiyat sınıra %1 yaklaştığında uyarır. Sonra %0.9, %0.8, %0.7 diye yaklaştıkça yeni mesaj atar.",
    scannerDesc: "16 watchlist hissesini ve Polymarket tahtalarını geçmiş 60 günlük verilerle analiz eder.",
    lastScan: "Son Tarama",
    scanNowButton: "🔍 Şimdi Tara",
    scanningButton: "Taranıyor...",
    offHoursWarning: "Piyasa saatleri dışındasınız:",
    hepsi: "Hepsi",
    safeBets: "💎 İmkansız / Güvenli Bahisler",
    ordersAt99: "📦 99¢ Emir Olanlar",
    noOpportunity: "Taramayı başlatmak için yukarıdaki 'Şimdi Tara' butonuna basın.",
    noOpportunityFiltered: "Bu filtreye uygun bir fırsat bulunamadı.",
    changeYesterday: "Düne Göre Değişim",
    marketBoard: "Polymarket Tahtası",
    noMarket: "Pazar Yok",
    historicalAnalysis: "Tarihsel Analiz (60 Gün)",
    reversalRate: "Ters Dönüş Oranı",
    worstCase: "En Kötü Senaryo",
    worstCaseNever: "Ters dönmemiş ✅",
    orderBook: "📦 Emir Kitabı",
    activeOrders: "ALIM AKTİF",
    noOrders: "EMİR YOK",
    cheapestAsk: "En Ucuz Teklif (Ask)",
    askSize: "Satış Emir Büyüklüğü",
    ordersAt99Label: "99¢'daki Emirler",
    noOrdersCLOB: "CLOB satış emri yok",
    recommendation: "Tavsiye",
    yield: "kâr",
    tradeButton: "İşlem Yap ↗",
    confirmDelete: "Emin misiniz?",
    deleteSuccess: "Pozisyon silindi.",
    errorOccurred: "Hata oluştu.",
    serverError: "Sunucuya bağlanılamadı.",
    scanError: "Tarama sırasında bir hata oluştu.",
    loading: "Yükleniyor...",
    cmeRollAlert: "ROLLOVER ALFA UYARISI",
    noTrades: "Henüz analiz edilmiş bir işlem bulunamadı.",
    tradeDetails: "İşlem Detayları",
    expectedYield: "Beklenen Getiri",
    statsRefPrice: "Referans/Hedef Fiyat",
    statsCurrentPrice: "Canlı Fiyat",
    statsMinsLeft: "Kapanışa Kalan Süre",
    statsFlips: "Pre-market Flip Sayısı",
    statsNewsAlerts: "Ekonomik Veri Uyarıları"
  },
  en: {
    title: "Poly Up/Down Tracker",
    subtitle: "Real-time market tracking and alert system",
    stocksClose: "Stocks Close: 16:00 ET",
    commoditiesClose: "Commodities Close: 17:00 ET",
    tabPositions: "📈 Position Tracking & Alarms",
    tabScanner: "🎯 Finance Signal Scanner",
    tabTrades: "🤖 AI Trade Commentary",
    newTag: "New",
    activePositions: "Active Positions",
    noActivePositions: "No active positions yet. You can add positions via Telegram using the /up SPX command.",
    refPriceLabel: "Reference (Yesterday's Close)",
    livePythPrice: "Live Pyth Price",
    statusLabel: "Status",
    winning: "WINNING",
    losing: "LOSING",
    quickAddTitle: "Polymarket Quick Add",
    addAlertTitle: "Add New Alarm",
    symbolLabel: "Symbol (e.g. SPX, PLTR)",
    typeLabel: "Type",
    directionLabel: "Direction",
    dailyClose: "Daily (Close)",
    openingOpen: "Opening (Open)",
    up: "Up (UP)",
    down: "Down (DOWN)",
    addAlertButton: "Add Alarm",
    addingButton: "Adding...",
    alertAdded: "Position added and Telegram notified!",
    tgSettingsTitle: "Telegram Alert Settings",
    dangerZoneLabel: "Danger Zone (%)",
    dangerZoneDesc: "Alerts begin when price gets this close to the reference line.",
    stepLabel: "Alert Step (%)",
    stepDesc: "When in the danger zone, a new message is sent at every step of this size.",
    saveSettings: "Save Settings",
    settingsSaved: "Settings saved!",
    settingsDesc: "Example: Danger Zone 1%, Step 0.1%; Alerts at 1% distance, then at 0.9%, 0.8%, 0.7% as price approaches reference line.",
    scannerDesc: "Analyzes 16 watchlist assets and Polymarket orderbooks using past 60 days of historical data.",
    lastScan: "Last Scan",
    scanNowButton: "🔍 Scan Now",
    scanningButton: "Scanning...",
    offHoursWarning: "You are outside regular trading hours:",
    hepsi: "All",
    safeBets: "💎 Safe / Impossible Bets",
    ordersAt99: "📦 99¢ Orders",
    noOpportunity: "Press the 'Scan Now' button above to start scanning.",
    noOpportunityFiltered: "No opportunities found matching this filter.",
    changeYesterday: "Change vs Yesterday",
    marketBoard: "Polymarket Board",
    noMarket: "No Market",
    historicalAnalysis: "Historical Analysis (60 Days)",
    reversalRate: "Reversal Rate",
    worstCase: "Worst Case Scenario",
    worstCaseNever: "Never reversed ✅",
    orderBook: "📦 Order Book",
    activeOrders: "BUY ACTIVE",
    noOrders: "NO ORDERS",
    cheapestAsk: "Cheapest Offer (Ask)",
    askSize: "Ask Order Size",
    ordersAt99Label: "Orders at 99¢",
    noOrdersCLOB: "No CLOB ask orders",
    recommendation: "Recommendation",
    yield: "yield",
    tradeButton: "Trade ↗",
    confirmDelete: "Are you sure?",
    deleteSuccess: "Position deleted.",
    errorOccurred: "An error occurred.",
    serverError: "Could not connect to server.",
    scanError: "An error occurred during scanning.",
    loading: "Loading...",
    cmeRollAlert: "ROLLOVER ALPHA ALERT",
    noTrades: "No analyzed trades found yet.",
    tradeDetails: "Trade Details",
    expectedYield: "Expected Yield",
    statsRefPrice: "Ref/Target Price",
    statsCurrentPrice: "Live Price",
    statsMinsLeft: "Minutes to Close",
    statsFlips: "Pre-market Flips",
    statsNewsAlerts: "Scheduled Economic Events"
  }
};

function App() {
  const [lang, setLang] = useState<'tr' | 'en'>(() => {
    return (localStorage.getItem('lang') as 'tr' | 'en') || 'tr';
  });

  const t = TRANSLATIONS[lang];

  useEffect(() => {
    localStorage.setItem('lang', lang);
  }, [lang]);

  const [positions, setPositions] = useState<Position[]>([]);
  const [settings, setSettings] = useState<Settings>({ warning_zone_pct: 1.0, step_pct: 0.1 });
  const [trades, setTrades] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  
  // New Position State
  const [newSymbol, setNewSymbol] = useState('');
  const [newDirection, setNewDirection] = useState('UP');
  const [newBetType, setNewBetType] = useState('close');
  const [isAdding, setIsAdding] = useState(false);

  // Scanner States
  const [activeTab, setActiveTab] = useState<'positions' | 'scanner' | 'trades'>('positions');
  const [scanResults, setScanResults] = useState<ScanResult[]>([]);
  const [scanning, setScanning] = useState(false);
  const [scanTime, setScanTime] = useState<string | null>(null);
  const [scannerFilter, setScannerFilter] = useState<'all' | 'safe' | '99'>('all');

  const fetchData = async () => {
    try {
      const [posRes, setRes, tradesRes] = await Promise.all([
        fetch(`${API_BASE}/positions`),
        fetch(`${API_BASE}/settings`),
        fetch(`${API_BASE}/trades`)
      ]);
      
      if (posRes.ok) setPositions(await posRes.json());
      if (setRes.ok) setSettings(await setRes.json());
      if (tradesRes.ok) setTrades(await tradesRes.json());
    } catch (err) {
      console.error("Failed to fetch data:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5000); // Refresh every 5s
    return () => clearInterval(interval);
  }, []);

  const handleSaveSettings = async () => {
    try {
      await fetch(`${API_BASE}/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings)
      });
      alert(t.settingsSaved);
    } catch (err) {
      alert(t.errorOccurred);
    }
  };

  const handleAddPosition = async () => {
    if (!newSymbol.trim()) return;
    setIsAdding(true);
    try {
      const res = await fetch(`${API_BASE}/positions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: newSymbol.trim(),
          direction: newDirection,
          bet_type: newBetType
        })
      });
      
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || t.errorOccurred);
      
      setNewSymbol('');
      fetchData();
      alert(t.alertAdded);
    } catch (err: any) {
      alert(err.message);
    } finally {
      setIsAdding(false);
    }
  };

  const handleQuickAdd = async (symbol: string, direction: string, bet_type: string) => {
    setIsAdding(true);
    try {
      const res = await fetch(`${API_BASE}/positions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, direction, bet_type })
      });
      
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || t.errorOccurred);
      
      fetchData();
      alert(lang === 'tr' 
        ? `${symbol} ${direction} pozisyonu eklendi ve Telegram'a bildirildi!`
        : `${symbol} ${direction} position added and Telegram notified!`
      );
    } catch (err: any) {
      alert(err.message);
    } finally {
      setIsAdding(false);
    }
  };

  const deletePosition = async (id: number) => {
    if (!confirm(t.confirmDelete)) return;
    try {
      await fetch(`${API_BASE}/positions/${id}`, { method: 'DELETE' });
      fetchData();
    } catch (err) {
      console.error(err);
    }
  };

  const handleScanNow = async () => {
    setScanning(true);
    try {
      const res = await fetch(`${API_BASE}/scan-now`);
      if (res.ok) {
        const data = await res.json();
        setScanResults(data);
        setScanTime(new Date().toLocaleTimeString(lang === 'tr' ? 'tr-TR' : 'en-US'));
      } else {
        alert(t.scanError);
      }
    } catch (err) {
      console.error(err);
      alert(t.serverError);
    } finally {
      setScanning(false);
    }
  };

  const filteredScanResults = scanResults.filter(r => {
    if (scannerFilter === 'safe') return r.is_safe_bet || r.is_impossible;
    if (scannerFilter === '99') return r.has_orders_at_99;
    return true;
  });

  const containsOffHours = scanResults.some(r => r.is_off_hours);

  if (loading) return <div className="min-h-screen flex items-center justify-center bg-poly-dark text-white">{t.loading}</div>;

  return (
    <div className="min-h-screen bg-poly-dark text-poly-text p-6">
      <div className="max-w-6xl mx-auto space-y-8">
        
        {/* Header */}
        <header className="flex justify-between items-end border-b border-poly-border pb-4">
          <div>
            <h1 className="text-3xl font-bold tracking-tight text-white flex items-center gap-2">
              <span className="bg-blue-600 px-2 py-1 rounded text-sm mr-2">P</span>
              {t.title}
            </h1>
            <p className="text-poly-textMuted mt-1">{t.subtitle}</p>
          </div>
          
          <div className="flex items-center gap-4">
            {/* TR / EN Language Switch Toggle */}
            <div className="flex bg-poly-card border border-poly-border rounded-lg p-0.5 shrink-0 shadow-inner">
              <button
                onClick={() => setLang('tr')}
                className={`px-3 py-1.5 rounded-md text-xs font-bold transition-all duration-200 ${
                  lang === 'tr'
                    ? 'bg-blue-600 text-white shadow-md'
                    : 'text-poly-textMuted hover:text-white'
                }`}
              >
                TR
              </button>
              <button
                onClick={() => setLang('en')}
                className={`px-3 py-1.5 rounded-md text-xs font-bold transition-all duration-200 ${
                  lang === 'en'
                    ? 'bg-blue-600 text-white shadow-md'
                    : 'text-poly-textMuted hover:text-white'
                }`}
              >
                EN
              </button>
            </div>
            
            <div className="text-right text-sm text-poly-textMuted hidden sm:block">
              <p>{t.stocksClose}</p>
              <p>{t.commoditiesClose}</p>
            </div>
          </div>
        </header>

        {/* Navigation Tabs */}
        <div className="flex border-b border-poly-border">
          <button
            onClick={() => setActiveTab('positions')}
            className={`px-6 py-3 font-semibold transition-all border-b-2 flex items-center gap-2 ${
              activeTab === 'positions'
                ? 'border-blue-500 text-blue-500 bg-blue-500/5'
                : 'border-transparent text-poly-textMuted hover:text-white'
            }`}
          >
            {t.tabPositions}
          </button>
          <button
            onClick={() => {
              setActiveTab('scanner');
              if (scanResults.length === 0) {
                handleScanNow();
              }
            }}
            className={`px-6 py-3 font-semibold transition-all border-b-2 flex items-center gap-2 ${
              activeTab === 'scanner'
                ? 'border-blue-500 text-blue-500 bg-blue-500/5'
                : 'border-transparent text-poly-textMuted hover:text-white'
            }`}
          >
            {t.tabScanner}
            <span className="bg-green-500/20 text-green-400 text-xs px-2 py-0.5 rounded-full font-bold animate-pulse">
              {t.newTag}
            </span>
          </button>
          <button
            onClick={() => setActiveTab('trades')}
            className={`px-6 py-3 font-semibold transition-all border-b-2 flex items-center gap-2 ${
              activeTab === 'trades'
                ? 'border-blue-500 text-blue-500 bg-blue-500/5'
                : 'border-transparent text-poly-textMuted hover:text-white'
            }`}
          >
            {t.tabTrades}
            {trades.length > 0 && (
              <span className="bg-blue-500 text-white text-xs px-1.5 py-0.5 rounded-full font-bold">
                {trades.length}
              </span>
            )}
          </button>
        </div>

        {activeTab === 'positions' && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
            
            {/* Main Content: Positions */}
            <div className="lg:col-span-2 space-y-4">
              <h2 className="text-xl font-semibold border-l-4 border-blue-500 pl-3">{t.activePositions}</h2>
              
              {positions.length === 0 ? (
                <div className="bg-poly-card border border-poly-border rounded-lg p-8 text-center text-poly-textMuted">
                  {t.noActivePositions}
                </div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {positions.map(p => (
                    <div key={p.id} className={`relative bg-poly-card border rounded-lg p-5 transition-all ${p.is_winning ? 'border-poly-border hover:border-poly-up/50' : 'border-poly-down/30 shadow-[0_0_15px_rgba(255,61,0,0.1)]'}`}>
                      <div className="flex justify-between items-start">
                        <div className="flex items-center gap-2">
                          <span className={`px-2 py-0.5 rounded text-xs font-bold ${p.direction === 'UP' ? 'bg-poly-up/20 text-poly-up' : 'bg-poly-down/20 text-poly-down'}`}>
                            {p.direction}
                          </span>
                          <h3 className="font-bold text-lg">{p.symbol}</h3>
                        </div>
                        <button onClick={() => deletePosition(p.id)} className="text-poly-textMuted hover:text-white transition-colors">
                          ✕
                        </button>
                      </div>
                      
                      <div className="mt-4 space-y-2 text-sm">
                        <div className="flex justify-between">
                          <span className="text-poly-textMuted">{t.refPriceLabel}</span>
                          <span className="font-mono">${p.ref_price.toFixed(4)}</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-poly-textMuted">{t.livePythPrice}</span>
                          <span className="font-mono font-medium">${p.current_price?.toFixed(4) || '---'}</span>
                        </div>
                      </div>
                      
                      <div className="mt-4 pt-4 border-t border-poly-border flex justify-between items-center">
                        <div className="text-sm">
                          {t.statusLabel}: <span className={`font-semibold ${p.is_winning ? 'text-poly-up' : 'text-poly-down'}`}>
                            {p.is_winning ? t.winning : t.losing}
                          </span>
                        </div>
                        <div className={`font-mono font-bold ${p.is_winning ? 'text-poly-up' : 'text-poly-down'}`}>
                          {p.diff_pct !== null ? `${p.diff_pct > 0 ? '+' : ''}${p.diff_pct.toFixed(2)}%` : ''}
                        </div>
                      </div>
                      
                      {/* Danger bar indicator */}
                      {!p.is_winning && p.diff_pct !== null && Math.abs(p.diff_pct) <= settings.warning_zone_pct && (
                        <div className="absolute bottom-0 left-0 h-1 bg-poly-down rounded-b-lg w-full animate-pulse"></div>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {/* Quick Markets Section */}
              <div className="mt-12 pt-8 border-t border-poly-border">
                <h2 className="text-xl font-semibold border-l-4 border-blue-400 pl-3 mb-6">{t.quickAddTitle}</h2>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                  {PREDEFINED_MARKETS.map((market, idx) => (
                    <div key={idx} className="bg-poly-card border border-poly-border rounded-lg p-4 hover:border-gray-500 transition-colors flex flex-col justify-between min-h-[140px]">
                      <div className="flex items-start gap-3 mb-4">
                        <div className="w-8 h-8 rounded-full bg-gray-800 flex items-center justify-center text-xs font-bold shrink-0">
                          {market.symbol.substring(0, 2)}
                        </div>
                        <h3 className="font-semibold text-sm leading-tight text-white">{market.name}</h3>
                      </div>
                      
                      <div className="grid grid-cols-2 gap-2 mt-auto">
                        <button 
                          onClick={() => handleQuickAdd(market.symbol, 'UP', market.bet_type)}
                          disabled={isAdding}
                          className="py-2 rounded bg-poly-up/10 text-poly-up border border-poly-up/20 hover:bg-poly-up hover:text-white transition-colors text-sm font-semibold disabled:opacity-50"
                        >
                          {lang === 'tr' ? 'Yukarı' : 'Up'}
                        </button>
                        <button 
                          onClick={() => handleQuickAdd(market.symbol, 'DOWN', market.bet_type)}
                          disabled={isAdding}
                          className="py-2 rounded bg-poly-down/10 text-poly-down border border-poly-down/20 hover:bg-poly-down hover:text-white transition-colors text-sm font-semibold disabled:opacity-50"
                        >
                          {lang === 'tr' ? 'Aşağı' : 'Down'}
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {/* Sidebar: Settings & Add Position */}
            <div className="space-y-6">
              
              {/* Add Position Form */}
              <div className="space-y-4">
                <h2 className="text-xl font-semibold border-l-4 border-green-500 pl-3">{t.addAlertTitle}</h2>
                <div className="bg-poly-card border border-poly-border rounded-lg p-5 space-y-4">
                  <div>
                    <label className="block text-sm text-poly-textMuted mb-1">{t.symbolLabel}</label>
                    <input 
                      type="text" 
                      placeholder="SPX"
                      value={newSymbol}
                      onChange={(e) => setNewSymbol(e.target.value.toUpperCase())}
                      className="w-full bg-poly-dark border border-poly-border rounded p-2 text-white font-mono focus:border-green-500 outline-none uppercase"
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="block text-sm text-poly-textMuted mb-1">{t.typeLabel}</label>
                      <select 
                        value={newBetType}
                        onChange={(e) => setNewBetType(e.target.value)}
                        className="w-full bg-poly-dark border border-poly-border rounded p-2 text-white focus:border-green-500 outline-none"
                      >
                        <option value="close">{t.dailyClose}</option>
                        <option value="open">{t.openingOpen}</option>
                      </select>
                    </div>
                    <div>
                      <label className="block text-sm text-poly-textMuted mb-1">{t.directionLabel}</label>
                      <select 
                        value={newDirection}
                        onChange={(e) => setNewDirection(e.target.value)}
                        className="w-full bg-poly-dark border border-poly-border rounded p-2 text-white focus:border-green-500 outline-none"
                      >
                        <option value="UP">{t.up}</option>
                        <option value="DOWN">{t.down}</option>
                      </select>
                    </div>
                  </div>
                  <button 
                    onClick={handleAddPosition}
                    disabled={isAdding || !newSymbol.trim()}
                    className="w-full bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white font-medium py-2 px-4 rounded transition-colors"
                  >
                    {isAdding ? t.addingButton : t.addAlertButton}
                  </button>
                </div>
              </div>

              {/* Settings */}
              <div className="space-y-4">
                <h2 className="text-xl font-semibold border-l-4 border-orange-500 pl-3">{t.tgSettingsTitle}</h2>
                <div className="bg-poly-card border border-poly-border rounded-lg p-5 space-y-6">
                
                <div>
                  <label className="block text-sm text-poly-textMuted mb-2">{t.dangerZoneLabel}</label>
                  <div className="flex items-center gap-2">
                    <input 
                      type="number" 
                      step="0.1"
                      value={settings.warning_zone_pct}
                      onChange={(e) => setSettings({...settings, warning_zone_pct: parseFloat(e.target.value)})}
                      className="w-full bg-poly-dark border border-poly-border rounded p-2 text-white font-mono focus:border-blue-500 outline-none"
                    />
                    <span className="text-poly-textMuted">%</span>
                  </div>
                  <p className="text-xs text-poly-textMuted mt-1">{t.dangerZoneDesc}</p>
                </div>

                <div>
                  <label className="block text-sm text-poly-textMuted mb-2">{t.stepLabel}</label>
                  <div className="flex items-center gap-2">
                    <input 
                      type="number" 
                      step="0.01"
                      value={settings.step_pct}
                      onChange={(e) => setSettings({...settings, step_pct: parseFloat(e.target.value)})}
                      className="w-full bg-poly-dark border border-poly-border rounded p-2 text-white font-mono focus:border-blue-500 outline-none"
                    />
                    <span className="text-poly-textMuted">%</span>
                  </div>
                  <p className="text-xs text-poly-textMuted mt-1">{t.stepDesc}</p>
                </div>

                <button 
                  onClick={handleSaveSettings}
                  className="w-full bg-blue-600 hover:bg-blue-700 text-white font-medium py-2 px-4 rounded transition-colors"
                >
                  {t.saveSettings}
                </button>
                
                <div className="mt-4 p-3 bg-poly-dark rounded border border-poly-border/50 text-xs text-poly-textMuted">
                  {t.settingsDesc}
                </div>
              </div>
            </div>
            </div>

          </div>
        )}

        {activeTab === 'scanner' && (
          /* Scanner Screen Tab */
          <div className="space-y-6">
            <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 bg-poly-card border border-poly-border rounded-xl p-5">
              <div>
                <h2 className="text-xl font-bold text-white flex items-center gap-2">
                  <span>{t.tabScanner}</span>
                </h2>
                <p className="text-sm text-poly-textMuted mt-1">
                  {t.scannerDesc}
                </p>
                {scanTime && (
                  <p className="text-xs text-green-400 mt-1 font-mono">
                    {t.lastScan}: {scanTime}
                  </p>
                )}
              </div>
              
              <button
                onClick={handleScanNow}
                disabled={scanning}
                className="w-full md:w-auto bg-blue-600 hover:bg-blue-700 disabled:bg-blue-800 disabled:opacity-50 text-white font-bold py-3 px-8 rounded-lg flex items-center justify-center gap-2 transition-all shadow-[0_0_15px_rgba(37,99,235,0.3)] hover:shadow-[0_0_20px_rgba(37,99,235,0.5)]"
              >
                {scanning ? (
                  <>
                    <svg className="animate-spin h-5 w-5 text-white" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                    </svg>
                    {t.scanningButton}
                  </>
                ) : (
                  <>
                    {t.scanNowButton}
                  </>
                )}
              </button>
            </div>

            {containsOffHours && (
              <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg p-4 flex gap-3 items-center">
                <span className="text-amber-400 text-xl font-bold shrink-0">⚠️</span>
                <p className="text-xs text-amber-300">
                  <strong>{lang === 'tr' ? 'Piyasa saatleri dışındasınız:' : 'You are outside regular trading hours:'}</strong> {scanResults[0]?.off_hours_reason || "ABD piyasaları kapalı veya henüz açılmamıştır."}
                </p>
              </div>
            )}

            {/* Filter controls */}
            <div className="flex gap-2 border-b border-poly-border/50 pb-4">
              <button
                onClick={() => setScannerFilter('all')}
                className={`px-4 py-1.5 rounded-full text-xs font-semibold transition-all ${
                  scannerFilter === 'all'
                    ? 'bg-blue-600 text-white font-bold'
                    : 'bg-poly-card text-poly-textMuted border border-poly-border hover:text-white'
                }`}
              >
                {t.hepsi} ({scanResults.length})
              </button>
              <button
                onClick={() => setScannerFilter('safe')}
                className={`px-4 py-1.5 rounded-full text-xs font-semibold transition-all flex items-center gap-1 ${
                  scannerFilter === 'safe'
                    ? 'bg-green-600 text-white font-bold'
                    : 'bg-poly-card text-poly-textMuted border border-poly-border hover:text-white'
                }`}
              >
                {t.safeBets} ({scanResults.filter(r => r.is_safe_bet || r.is_impossible).length})
              </button>
              <button
                onClick={() => setScannerFilter('99')}
                className={`px-4 py-1.5 rounded-full text-xs font-semibold transition-all flex items-center gap-1 ${
                  scannerFilter === '99'
                    ? 'bg-amber-600 text-white font-bold'
                    : 'bg-poly-card text-poly-textMuted border border-poly-border hover:text-white'
                }`}
              >
                {t.ordersAt99} ({scanResults.filter(r => r.has_orders_at_99).length})
              </button>
            </div>

            {/* Scanned opportunities grid */}
            {filteredScanResults.length === 0 ? (
              <div className="bg-poly-card border border-poly-border rounded-xl p-12 text-center text-poly-textMuted">
                {scanResults.length === 0
                  ? t.noOpportunity
                  : t.noOpportunityFiltered}
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                {filteredScanResults.map(r => (
                  <div
                    key={r.symbol}
                    className={`bg-poly-card border rounded-xl p-5 hover:border-gray-500 transition-all flex flex-col justify-between ${
                      r.is_impossible && r.has_orders_at_99
                        ? 'border-green-500/50 shadow-[0_0_20px_rgba(0,200,83,0.15)] bg-gradient-to-br from-poly-card to-green-950/20'
                        : r.is_impossible
                        ? 'border-blue-500/40 bg-gradient-to-br from-poly-card to-blue-950/10'
                        : 'border-poly-border'
                    }`}
                  >
                    {/* Card Header */}
                    <div>
                      <div className="flex justify-between items-start">
                        <div className="flex items-center gap-2">
                          <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${r.direction === 'UP' ? 'bg-poly-up/20 text-poly-up' : 'bg-poly-down/20 text-poly-down'}`}>
                            {r.direction === 'UP' ? (lang === 'tr' ? '📈 YUKARI' : '📈 UP') : (lang === 'tr' ? '📉 AŞAĞI' : '📉 DOWN')}
                          </span>
                          <h3 className="font-bold text-xl text-white">{r.symbol}</h3>
                        </div>

                        <div className="flex flex-col gap-1 items-end shrink-0">
                          {r.is_impossible && (
                            <span className="bg-green-500 text-poly-dark text-[9px] font-black px-2 py-0.5 rounded-full uppercase tracking-wider animate-pulse">
                              {lang === 'tr' ? '💎 İMKANSIZ' : '💎 IMPOSSIBLE'}
                            </span>
                          )}
                          {r.has_orders_at_99 && (
                            <span className="bg-amber-500 text-poly-dark text-[9px] font-black px-2 py-0.5 rounded-full uppercase tracking-wider">
                              {lang === 'tr' ? '📦 99¢ EMİR' : '📦 99¢ ORDER'}
                            </span>
                          )}
                        </div>
                      </div>
                      <p className="text-xs text-poly-textMuted mt-1">
                        {t.changeYesterday}: <span className={`font-mono font-bold ${r.direction === 'UP' ? 'text-poly-up' : 'text-poly-down'}`}>{r.diff_pct > 0 ? '+' : ''}{r.diff_pct.toFixed(2)}%</span>
                      </p>
                    </div>

                    {/* Price details */}
                    <div className="space-y-1.5 text-xs border-t border-poly-border/50 pt-3 mt-3">
                      <div className="flex justify-between">
                        <span className="text-poly-textMuted">{t.livePythPrice}:</span>
                        <span className="font-mono text-white">${r.current_price.toFixed(4)}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-poly-textMuted">{t.refPriceLabel}:</span>
                        <span className="font-mono text-white">${r.ref_price.toFixed(4)}</span>
                      </div>
                      <div className="flex justify-between items-center">
                        <span className="text-poly-textMuted">{t.marketBoard}:</span>
                        <span className="font-medium text-white">
                          {r.poly.slug ? (
                            <span className="flex items-center gap-1.5 font-mono">
                              <span className={r.direction === 'UP' ? 'text-green-400 font-bold' : ''}>U: {Math.round(r.poly.up_price * 100)}¢</span>
                              <span className="text-poly-border">|</span>
                              <span className={r.direction === 'DOWN' ? 'text-red-400 font-bold' : ''}>D: {Math.round(r.poly.down_price * 100)}¢</span>
                            </span>
                          ) : (
                            <span className="text-poly-textMuted italic text-xs">{t.noMarket}</span>
                          )}
                        </span>
                      </div>
                    </div>

                    {/* Historical Risk analysis */}
                    <div className="bg-poly-dark/50 rounded-lg p-3 mt-3 border border-poly-border/30 space-y-1 text-xs">
                      <div className="flex justify-between items-center">
                        <span className="text-poly-textMuted font-medium">{t.historicalAnalysis}</span>
                        <span className="font-bold text-amber-400">{r.historical.confidence_stars}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-poly-textMuted">{t.reversalRate}:</span>
                        <span className={`font-semibold font-mono ${r.historical.reversed_count === 0 ? 'text-poly-up' : r.historical.reversed_count === 1 ? 'text-yellow-400' : 'text-poly-down'}`}>
                          {r.historical.reversed_count}/{r.historical.total_similar_days} {lang === 'tr' ? 'gün' : 'days'} (%{r.historical.reversal_rate.toFixed(1)})
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-poly-textMuted">{t.worstCase}:</span>
                        <span className="font-mono text-white">
                          {r.historical.worst_case !== 0 
                            ? (r.symbol === 'WTI' || ['XAU', 'XAG'].some(c => r.symbol.includes(c))
                                ? `${r.historical.worst_case > 0 ? '+' : ''}$${r.historical.worst_case.toFixed(2)}`
                                : `${r.historical.worst_case > 0 ? '+' : ''}%${r.historical.worst_case.toFixed(2)}`
                              )
                            : t.worstCaseNever}
                        </span>
                      </div>
                    </div>

                    {/* Orderbook details */}
                    <div className="bg-amber-500/5 border border-amber-500/20 rounded-lg p-3 mt-3 space-y-1 text-xs">
                      <div className="flex justify-between items-center">
                        <span className="text-amber-400 font-semibold flex items-center gap-1">
                          <span>{t.orderBook}</span>
                        </span>
                        <span className={`text-[9px] font-bold px-1.5 py-0.2 rounded font-mono ${r.poly.has_orders_at_99 ? 'bg-amber-500/20 text-amber-300' : 'bg-gray-800 text-gray-400'}`}>
                          {r.poly.has_orders_at_99 ? t.activeOrders : t.noOrders}
                        </span>
                      </div>
                      
                      {r.poly.best_ask !== null ? (
                        <>
                          <div className="flex justify-between">
                            <span className="text-poly-textMuted">{t.cheapestAsk}:</span>
                            <span className="font-bold text-white font-mono">{Math.round(r.poly.best_ask * 100)}¢</span>
                          </div>
                          <div className="flex justify-between">
                            <span className="text-poly-textMuted">{t.askSize}:</span>
                            <span className="font-semibold text-white font-mono">${Math.round(r.poly.depth_at_best).toLocaleString(lang === 'tr' ? 'tr-TR' : 'en-US')}</span>
                          </div>
                          {r.poly.depth_at_99 > 0 && (
                            <div className="flex justify-between border-t border-amber-500/10 pt-1 mt-1">
                              <span className="text-amber-300 font-medium">{t.ordersAt99Label}:</span>
                              <span className="font-bold text-amber-400 font-mono">${Math.round(r.poly.depth_at_99).toLocaleString(lang === 'tr' ? 'tr-TR' : 'en-US')}</span>
                            </div>
                          )}
                        </>
                      ) : (
                        <div className="text-center text-poly-textMuted italic py-1 text-[11px]">{t.noOrdersCLOB}</div>
                      )}
                    </div>

                    {/* Action buttons */}
                    <div className="mt-4 pt-3 border-t border-poly-border/50 flex justify-between items-center text-xs">
                      <div>
                        {r.poly.safe_outcome_price > 0 && (
                          <span className="text-poly-textMuted text-[11px]">
                            {t.recommendation}: <strong className="text-white font-bold">{Math.round(r.poly.safe_outcome_price * 100)}¢ → $1.00</strong> (%{(((1.0 - r.poly.safe_outcome_price) / r.poly.safe_outcome_price) * 100).toFixed(1)} {t.yield})
                          </span>
                        )}
                      </div>
                      {r.poly.slug && (
                        <a
                          href={`https://polymarket.com/event/${r.poly.slug}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-blue-400 hover:text-blue-300 font-bold underline flex items-center gap-0.5 shrink-0"
                        >
                          {t.tradeButton}
                        </a>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {activeTab === 'trades' && (
          <div className="space-y-6">
            <div className="bg-poly-card border border-poly-border rounded-xl p-5">
              <h2 className="text-xl font-bold text-white flex items-center gap-2">
                <span>{t.tabTrades}</span>
              </h2>
              <p className="text-sm text-poly-textMuted mt-1">
                {lang === 'tr' 
                  ? 'Takip edilen Polymarket hesaplarının son işlemleri ve yapay zeka analizleri.' 
                  : 'Latest trades and quantitative AI analysis of tracked Polymarket wallets.'}
              </p>
            </div>

            {trades.length === 0 ? (
              <div className="bg-poly-card border border-poly-border rounded-xl p-12 text-center text-poly-textMuted">
                {t.noTrades}
              </div>
            ) : (
              <div className="space-y-6">
                {trades.map(trade => {
                  let analysis = null;
                  if (trade.analysis_json) {
                    try {
                      analysis = JSON.parse(trade.analysis_json);
                    } catch (e) {
                      console.error("Failed to parse analysis JSON:", e);
                    }
                  }
                  
                  const expectedYield = trade.price > 0 && trade.price < 1 
                    ? ((1 - trade.price) / trade.price) * 100 
                    : 0;

                  // Clean the comment of HTML tags that React doesn't support directly
                  // and translate to JSX or render safely.
                  // Telegram allows <b>, <i>, <code>, <a>, <u> etc. which standard HTML supports.
                  // Let's replace newlines with <br /> and keep HTML formatting.
                  const cleanCommentHtml = (trade.ai_comment || "")
                    .replace(/\n/g, "<br />");

                  return (
                    <div key={trade.tx_hash} className="bg-poly-card border border-poly-border rounded-xl p-6 space-y-4 hover:border-gray-500 transition-all">
                      {/* Header */}
                      <div className="flex flex-col sm:flex-row justify-between sm:items-center gap-3 border-b border-poly-border pb-4">
                        <div>
                          <div className="flex items-center gap-2">
                            <span className="w-2.5 h-2.5 rounded-full bg-blue-500 animate-ping"></span>
                            <span className="font-bold text-white">{trade.telegram_tag}</span>
                            <span className="text-poly-textMuted text-xs font-mono">({trade.username})</span>
                          </div>
                          <h3 className="font-semibold text-white mt-1 text-base">{trade.title}</h3>
                        </div>
                        <div className="text-xs text-poly-textMuted text-left sm:text-right shrink-0">
                          <p className="font-mono">{new Date(trade.created_at).toLocaleString(lang === 'tr' ? 'tr-TR' : 'en-US')}</p>
                          {trade.tx_hash && (
                            <a 
                              href={`https://polygonscan.com/tx/${trade.tx_hash}`} 
                              target="_blank" 
                              rel="noopener noreferrer" 
                              className="text-blue-400 hover:text-blue-300 font-mono text-[10px] underline block mt-0.5"
                            >
                              {trade.tx_hash.substring(0, 10)}...
                            </a>
                          )}
                        </div>
                      </div>

                      {/* Trade Details Badges */}
                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 bg-poly-dark/40 p-3 rounded-lg border border-poly-border/30 text-xs">
                        <div>
                          <p className="text-poly-textMuted">{lang === 'tr' ? 'İşlem' : 'Side'}</p>
                          <p className="font-bold text-white flex items-center gap-1 mt-0.5">
                            <span className="px-1.5 py-0.5 rounded text-[10px] bg-green-500/20 text-green-400">{trade.side}</span>
                            <span className={`px-1.5 py-0.5 rounded text-[10px] ${trade.outcome.toUpperCase() === 'UP' || trade.outcome.toUpperCase() === 'YES' ? 'bg-poly-up/20 text-poly-up' : 'bg-poly-down/20 text-poly-down'}`}>
                              {trade.outcome}
                            </span>
                          </p>
                        </div>
                        <div>
                          <p className="text-poly-textMuted">{lang === 'tr' ? 'Miktar' : 'Contracts'}</p>
                          <p className="font-bold text-white font-mono mt-0.5">{Math.round(trade.size).toLocaleString()}</p>
                        </div>
                        <div>
                          <p className="text-poly-textMuted">{lang === 'tr' ? 'Ortalama Fiyat' : 'Avg Price'}</p>
                          <p className="font-bold text-white font-mono mt-0.5">${trade.price.toFixed(4)}</p>
                        </div>
                        <div>
                          <p className="text-poly-textMuted">{t.expectedYield}</p>
                          <p className="font-bold text-green-400 font-mono mt-0.5">%{expectedYield.toFixed(1)}</p>
                        </div>
                      </div>

                      {/* Two column layouts for AI comment and Quant analysis */}
                      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 pt-2">
                        {/* AI Comment Column */}
                        <div className="lg:col-span-2 space-y-2">
                          <h4 className="text-sm font-bold text-white uppercase tracking-wider border-l-2 border-blue-500 pl-2">
                            {lang === 'tr' ? '🤖 Yapay Zeka Yorumu' : '🤖 AI Commentary'}
                          </h4>
                          <div 
                            className="text-poly-text text-sm leading-relaxed bg-poly-dark/20 p-4 rounded-lg border border-poly-border/20 prose prose-invert max-w-none"
                            dangerouslySetInnerHTML={{ __html: cleanCommentHtml }}
                          />
                        </div>

                        {/* Quant Statistics Column */}
                        {analysis && (
                          <div className="space-y-2">
                            <h4 className="text-sm font-bold text-white uppercase tracking-wider border-l-2 border-green-500 pl-2">
                              {lang === 'tr' ? '📊 Risk Analiz Raporu' : '📊 Quant Risk Report'}
                            </h4>
                            
                            <div className="bg-poly-dark/50 border border-poly-border/40 rounded-lg p-4 space-y-3 text-xs">
                              <div className="flex justify-between items-center border-b border-poly-border/40 pb-2">
                                <span className="font-bold text-white text-sm">{analysis.symbol}</span>
                                <span className="font-bold text-amber-400 text-sm">{analysis.confidence_stars || '❓'}</span>
                              </div>

                              <div className="space-y-2">
                                <div className="flex justify-between">
                                  <span className="text-poly-textMuted">{t.reversalRate}:</span>
                                  <span className={`font-mono font-bold ${analysis.reversed_count === 0 ? 'text-green-400' : 'text-amber-400'}`}>
                                    {analysis.reversed_count}/{analysis.total_similar_days} gün (%{analysis.reversal_rate?.toFixed(1)})
                                  </span>
                                </div>

                                <div className="flex justify-between">
                                  <span className="text-poly-textMuted">{t.statsRefPrice}:</span>
                                  <span className="font-mono text-white">${analysis.target_price?.toFixed(4)}</span>
                                </div>

                                <div className="flex justify-between">
                                  <span className="text-poly-textMuted">{t.statsCurrentPrice}:</span>
                                  <span className="font-mono text-white">${analysis.current_price?.toFixed(4)}</span>
                                </div>

                                {analysis.is_open_bet ? (
                                  <>
                                    <div className="flex justify-between">
                                      <span className="text-poly-textMuted">{t.statsFlips}:</span>
                                      <span className="font-mono text-white font-bold">{analysis.flips || 0}</span>
                                    </div>
                                    
                                    {analysis.economic_news && analysis.economic_news.length > 0 && (
                                      <div className="pt-2 border-t border-poly-border/40">
                                        <span className="text-amber-400 font-bold block mb-1">{t.statsNewsAlerts}:</span>
                                        <ul className="space-y-1 list-disc list-inside text-poly-textMuted">
                                          {analysis.economic_news.map((news: any, idx: number) => (
                                            <li key={idx} className="text-[10px] leading-tight">
                                              {news.time}: {news.event}
                                            </li>
                                          ))}
                                        </ul>
                                      </div>
                                    )}
                                  </>
                                ) : (
                                  <div className="flex justify-between">
                                    <span className="text-poly-textMuted">{t.statsMinsLeft}:</span>
                                    <span className="font-mono text-white">{analysis.minutes_left} dk</span>
                                  </div>
                                )}
                              </div>

                              <div className="pt-2 border-t border-poly-border/40 text-center">
                                <span className="text-[10px] font-bold text-green-400 bg-green-500/10 px-2 py-0.5 rounded">
                                  {analysis.confidence_label || 'VERİ YOK'}
                                </span>
                              </div>
                            </div>
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

      </div>
    </div>
  );
}

export default App;
