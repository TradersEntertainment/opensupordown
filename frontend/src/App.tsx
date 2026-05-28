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

function App() {
  const [positions, setPositions] = useState<Position[]>([]);
  const [settings, setSettings] = useState<Settings>({ warning_zone_pct: 1.0, step_pct: 0.1 });
  const [loading, setLoading] = useState(true);
  
  // New Position State
  const [newSymbol, setNewSymbol] = useState('');
  const [newDirection, setNewDirection] = useState('UP');
  const [newBetType, setNewBetType] = useState('close');
  const [isAdding, setIsAdding] = useState(false);

  // Scanner States
  const [activeTab, setActiveTab] = useState<'positions' | 'scanner'>('positions');
  const [scanResults, setScanResults] = useState<ScanResult[]>([]);
  const [scanning, setScanning] = useState(false);
  const [scanTime, setScanTime] = useState<string | null>(null);
  const [scannerFilter, setScannerFilter] = useState<'all' | 'safe' | '99'>('all');

  const fetchData = async () => {
    try {
      const [posRes, setRes] = await Promise.all([
        fetch(`${API_BASE}/positions`),
        fetch(`${API_BASE}/settings`)
      ]);
      
      if (posRes.ok) setPositions(await posRes.json());
      if (setRes.ok) setSettings(await setRes.json());
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
      alert("Ayarlar kaydedildi!");
    } catch (err) {
      alert("Hata oluştu.");
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
      if (!res.ok) throw new Error(data.detail || "Bir hata oluştu");
      
      setNewSymbol('');
      fetchData();
      alert("Pozisyon eklendi ve Telegram'a bildirildi!");
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
      if (!res.ok) throw new Error(data.detail || "Bir hata oluştu");
      
      fetchData();
      alert(`${symbol} ${direction} pozisyonu eklendi ve Telegram'a bildirildi!`);
    } catch (err: any) {
      alert(err.message);
    } finally {
      setIsAdding(false);
    }
  };

  const deletePosition = async (id: number) => {
    if (!confirm("Emin misiniz?")) return;
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
        setScanTime(new Date().toLocaleTimeString('tr-TR'));
      } else {
        alert("Tarama sırasında bir hata oluştu.");
      }
    } catch (err) {
      console.error(err);
      alert("Sunucuya bağlanılamadı.");
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

  if (loading) return <div className="min-h-screen flex items-center justify-center bg-poly-dark text-white">Yükleniyor...</div>;

  return (
    <div className="min-h-screen bg-poly-dark text-poly-text p-6">
      <div className="max-w-6xl mx-auto space-y-8">
        
        {/* Header */}
        <header className="flex justify-between items-end border-b border-poly-border pb-4">
          <div>
            <h1 className="text-3xl font-bold tracking-tight text-white flex items-center gap-2">
              <span className="bg-blue-600 px-2 py-1 rounded text-sm mr-2">P</span>
              Poly Up/Down Tracker
            </h1>
            <p className="text-poly-textMuted mt-1">Gerçek zamanlı pazar takip ve uyarı sistemi</p>
          </div>
          <div className="text-right text-sm text-poly-textMuted">
            <p>Hisseler Kapanış: 23:00 TR</p>
            <p>Emtialar Kapanış: 24:00 TR</p>
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
            📈 Pozisyon Takip ve Alarm
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
            🎯 Finance Signal Scanner
            <span className="bg-green-500/20 text-green-400 text-xs px-2 py-0.5 rounded-full font-bold animate-pulse">
              Yeni
            </span>
          </button>
        </div>

        {activeTab === 'positions' ? (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
            
            {/* Main Content: Positions */}
            <div className="lg:col-span-2 space-y-4">
              <h2 className="text-xl font-semibold border-l-4 border-blue-500 pl-3">Aktif Pozisyonlar</h2>
              
              {positions.length === 0 ? (
                <div className="bg-poly-card border border-poly-border rounded-lg p-8 text-center text-poly-textMuted">
                  Henüz aktif pozisyon yok. Telegram'dan <code className="bg-poly-dark px-2 py-1 rounded text-white">/up SPX</code> komutu ile pozisyon ekleyebilirsiniz.
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
                          <span className="text-poly-textMuted">Referans (Dünkü Kapanış)</span>
                          <span className="font-mono">${p.ref_price.toFixed(4)}</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-poly-textMuted">Anlık Pyth Fiyatı</span>
                          <span className="font-mono font-medium">${p.current_price?.toFixed(4) || '---'}</span>
                        </div>
                      </div>
                      
                      <div className="mt-4 pt-4 border-t border-poly-border flex justify-between items-center">
                        <div className="text-sm">
                          Durum: <span className={`font-semibold ${p.is_winning ? 'text-poly-up' : 'text-poly-down'}`}>
                            {p.is_winning ? 'KAZANIYOR' : 'KAYBEDİYOR'}
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
                <h2 className="text-xl font-semibold border-l-4 border-blue-400 pl-3 mb-6">Polymarket Hızlı Ekleme</h2>
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
                          Up
                        </button>
                        <button 
                          onClick={() => handleQuickAdd(market.symbol, 'DOWN', market.bet_type)}
                          disabled={isAdding}
                          className="py-2 rounded bg-poly-down/10 text-poly-down border border-poly-down/20 hover:bg-poly-down hover:text-white transition-colors text-sm font-semibold disabled:opacity-50"
                        >
                          Down
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
                <h2 className="text-xl font-semibold border-l-4 border-green-500 pl-3">Yeni Alarm Ekle</h2>
                <div className="bg-poly-card border border-poly-border rounded-lg p-5 space-y-4">
                  <div>
                    <label className="block text-sm text-poly-textMuted mb-1">Sembol (Örn: SPX, PLTR)</label>
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
                      <label className="block text-sm text-poly-textMuted mb-1">Tür</label>
                      <select 
                        value={newBetType}
                        onChange={(e) => setNewBetType(e.target.value)}
                        className="w-full bg-poly-dark border border-poly-border rounded p-2 text-white focus:border-green-500 outline-none"
                      >
                        <option value="close">Günlük (Close)</option>
                        <option value="open">Açılış (Open)</option>
                      </select>
                    </div>
                    <div>
                      <label className="block text-sm text-poly-textMuted mb-1">Yön</label>
                      <select 
                        value={newDirection}
                        onChange={(e) => setNewDirection(e.target.value)}
                        className="w-full bg-poly-dark border border-poly-border rounded p-2 text-white focus:border-green-500 outline-none"
                      >
                        <option value="UP">Yukarı (UP)</option>
                        <option value="DOWN">Aşağı (DOWN)</option>
                      </select>
                    </div>
                  </div>
                  <button 
                    onClick={handleAddPosition}
                    disabled={isAdding || !newSymbol.trim()}
                    className="w-full bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white font-medium py-2 px-4 rounded transition-colors"
                  >
                    {isAdding ? 'Ekleniyor...' : 'Alarm Ekle'}
                  </button>
                </div>
              </div>

              {/* Settings */}
              <div className="space-y-4">
                <h2 className="text-xl font-semibold border-l-4 border-orange-500 pl-3">Telegram Uyarı Ayarları</h2>
                <div className="bg-poly-card border border-poly-border rounded-lg p-5 space-y-6">
                
                <div>
                  <label className="block text-sm text-poly-textMuted mb-2">Tehlike Bölgesi (%)</label>
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
                  <p className="text-xs text-poly-textMuted mt-1">Fiyat referans çizgisine bu kadar yaklaştığında uyarılar başlar.</p>
                </div>

                <div>
                  <label className="block text-sm text-poly-textMuted mb-2">Uyarı Adımı (%)</label>
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
                  <p className="text-xs text-poly-textMuted mt-1">Tehlike bölgesindeyken her bu kadarlık harekette yeni mesaj atılır.</p>
                </div>

                <button 
                  onClick={handleSaveSettings}
                  className="w-full bg-blue-600 hover:bg-blue-700 text-white font-medium py-2 px-4 rounded transition-colors"
                >
                  Ayarları Kaydet
                </button>
                
                <div className="mt-4 p-3 bg-poly-dark rounded border border-poly-border/50 text-xs text-poly-textMuted">
                  <strong>Örnek:</strong> Tehlike Bölgesi %1, Adım %0.1 ise;<br/>
                  Fiyat sınıra %1 yaklaştığında uyarır. Sonra %0.9, %0.8, %0.7 diye yaklaştıkça yeni mesaj atar.
                </div>
              </div>
            </div>
            </div>

          </div>
        ) : (
          /* Scanner Screen Tab */
          <div className="space-y-6">
            <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 bg-poly-card border border-poly-border rounded-xl p-5">
              <div>
                <h2 className="text-xl font-bold text-white flex items-center gap-2">
                  <span>🎯 Finance Signal Scanner</span>
                </h2>
                <p className="text-sm text-poly-textMuted mt-1">
                  16 watchlist hissesini ve Polymarket tahtalarını geçmiş 60 günlük verilerle analiz eder.
                </p>
                {scanTime && (
                  <p className="text-xs text-green-400 mt-1 font-mono">
                    Son Tarama: {scanTime}
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
                    Taranıyor...
                  </>
                ) : (
                  <>
                    🔍 Şimdi Tara
                  </>
                )}
              </button>
            </div>

            {containsOffHours && (
              <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg p-4 flex gap-3 items-center">
                <span className="text-amber-400 text-xl font-bold shrink-0">⚠️</span>
                <p className="text-xs text-amber-300">
                  <strong>Piyasa saatleri dışındasınız:</strong> {scanResults[0]?.off_hours_reason || "ABD piyasaları kapalı veya henüz açılmamıştır."}
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
                Hepsi ({scanResults.length})
              </button>
              <button
                onClick={() => setScannerFilter('safe')}
                className={`px-4 py-1.5 rounded-full text-xs font-semibold transition-all flex items-center gap-1 ${
                  scannerFilter === 'safe'
                    ? 'bg-green-600 text-white font-bold'
                    : 'bg-poly-card text-poly-textMuted border border-poly-border hover:text-white'
                }`}
              >
                💎 İmkansız / Güvenli Bahisler ({scanResults.filter(r => r.is_safe_bet || r.is_impossible).length})
              </button>
              <button
                onClick={() => setScannerFilter('99')}
                className={`px-4 py-1.5 rounded-full text-xs font-semibold transition-all flex items-center gap-1 ${
                  scannerFilter === '99'
                    ? 'bg-amber-600 text-white font-bold'
                    : 'bg-poly-card text-poly-textMuted border border-poly-border hover:text-white'
                }`}
              >
                📦 99¢ Emir Olanlar ({scanResults.filter(r => r.has_orders_at_99).length})
              </button>
            </div>

            {/* Scanned opportunities grid */}
            {filteredScanResults.length === 0 ? (
              <div className="bg-poly-card border border-poly-border rounded-xl p-12 text-center text-poly-textMuted">
                {scanResults.length === 0
                  ? "Taramayı başlatmak için yukarıdaki 'Şimdi Tara' butonuna basın."
                  : "Bu filtreye uygun bir fırsat bulunamadı."}
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
                            {r.direction === 'UP' ? '📈 YUKARI' : '📉 AŞAĞI'}
                          </span>
                          <h3 className="font-bold text-xl text-white">{r.symbol}</h3>
                        </div>

                        <div className="flex flex-col gap-1 items-end shrink-0">
                          {r.is_impossible && (
                            <span className="bg-green-500 text-poly-dark text-[9px] font-black px-2 py-0.5 rounded-full uppercase tracking-wider animate-pulse">
                              💎 İMKANSIZ
                            </span>
                          )}
                          {r.has_orders_at_99 && (
                            <span className="bg-amber-500 text-poly-dark text-[9px] font-black px-2 py-0.5 rounded-full uppercase tracking-wider">
                              📦 99¢ EMİR
                            </span>
                          )}
                        </div>
                      </div>
                      <p className="text-xs text-poly-textMuted mt-1">
                        Düne Göre Değişim: <span className={`font-mono font-bold ${r.direction === 'UP' ? 'text-poly-up' : 'text-poly-down'}`}>{r.diff_pct > 0 ? '+' : ''}{r.diff_pct.toFixed(2)}%</span>
                      </p>
                    </div>

                    {/* Price details */}
                    <div className="space-y-1.5 text-xs border-t border-poly-border/50 pt-3 mt-3">
                      <div className="flex justify-between">
                        <span className="text-poly-textMuted">Anlık Pyth Fiyatı:</span>
                        <span className="font-mono text-white">${r.current_price.toFixed(4)}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-poly-textMuted">Dünkü Kapanış:</span>
                        <span className="font-mono text-white">${r.ref_price.toFixed(4)}</span>
                      </div>
                      <div className="flex justify-between items-center">
                        <span className="text-poly-textMuted">Polymarket Tahtası:</span>
                        <span className="font-medium text-white">
                          {r.poly.slug ? (
                            <span className="flex items-center gap-1.5 font-mono">
                              <span className={r.direction === 'UP' ? 'text-green-400 font-bold' : ''}>U: {Math.round(r.poly.up_price * 100)}¢</span>
                              <span className="text-poly-border">|</span>
                              <span className={r.direction === 'DOWN' ? 'text-red-400 font-bold' : ''}>D: {Math.round(r.poly.down_price * 100)}¢</span>
                            </span>
                          ) : (
                            <span className="text-poly-textMuted italic text-xs">Pazar Yok</span>
                          )}
                        </span>
                      </div>
                    </div>

                    {/* Historical Risk analysis */}
                    <div className="bg-poly-dark/50 rounded-lg p-3 mt-3 border border-poly-border/30 space-y-1 text-xs">
                      <div className="flex justify-between items-center">
                        <span className="text-poly-textMuted font-medium">Tarihsel Analiz (60 Gün)</span>
                        <span className="font-bold text-amber-400">{r.historical.confidence_stars}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-poly-textMuted">Ters Dönüş Oranı:</span>
                        <span className={`font-semibold font-mono ${r.historical.reversed_count === 0 ? 'text-poly-up' : r.historical.reversed_count === 1 ? 'text-yellow-400' : 'text-poly-down'}`}>
                          {r.historical.reversed_count}/{r.historical.total_similar_days} gün (%{r.historical.reversal_rate.toFixed(1)})
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-poly-textMuted">En Kötü Senaryo:</span>
                        <span className="font-mono text-white">
                          {r.historical.worst_case !== 0 ? `%${r.historical.worst_case > 0 ? '+' : ''}${r.historical.worst_case.toFixed(2)}` : 'Ters dönmemiş ✅'}
                        </span>
                      </div>
                    </div>

                    {/* Orderbook details */}
                    <div className="bg-amber-500/5 border border-amber-500/20 rounded-lg p-3 mt-3 space-y-1 text-xs">
                      <div className="flex justify-between items-center">
                        <span className="text-amber-400 font-semibold flex items-center gap-1">
                          <span>📦 Emir Kitabı</span>
                        </span>
                        <span className={`text-[9px] font-bold px-1.5 py-0.2 rounded font-mono ${r.poly.has_orders_at_99 ? 'bg-amber-500/20 text-amber-300' : 'bg-gray-800 text-gray-400'}`}>
                          {r.poly.has_orders_at_99 ? 'ALIM AKTİF' : 'EMİR YOK'}
                        </span>
                      </div>
                      
                      {r.poly.best_ask !== null ? (
                        <>
                          <div className="flex justify-between">
                            <span className="text-poly-textMuted">En Ucuz Teklif (Ask):</span>
                            <span className="font-bold text-white font-mono">{Math.round(r.poly.best_ask * 100)}¢</span>
                          </div>
                          <div className="flex justify-between">
                            <span className="text-poly-textMuted">Satış Emir Büyüklüğü:</span>
                            <span className="font-semibold text-white font-mono">${Math.round(r.poly.depth_at_best).toLocaleString('tr-TR')}</span>
                          </div>
                          {r.poly.depth_at_99 > 0 && (
                            <div className="flex justify-between border-t border-amber-500/10 pt-1 mt-1">
                              <span className="text-amber-300 font-medium">99¢'daki Emirler:</span>
                              <span className="font-bold text-amber-400 font-mono">${Math.round(r.poly.depth_at_99).toLocaleString('tr-TR')}</span>
                            </div>
                          )}
                        </>
                      ) : (
                        <div className="text-center text-poly-textMuted italic py-1 text-[11px]">CLOB satış emri yok</div>
                      )}
                    </div>

                    {/* Action buttons */}
                    <div className="mt-4 pt-3 border-t border-poly-border/50 flex justify-between items-center text-xs">
                      <div>
                        {r.poly.safe_outcome_price > 0 && (
                          <span className="text-poly-textMuted text-[11px]">
                            Tavsiye: <strong className="text-white font-bold">{Math.round(r.poly.safe_outcome_price * 100)}¢ → $1.00</strong> (%{(((1.0 - r.poly.safe_outcome_price) / r.poly.safe_outcome_price) * 100).toFixed(1)} kâr)
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
                          İşlem Yap ↗
                        </a>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

      </div>
    </div>
  );
}

export default App;
