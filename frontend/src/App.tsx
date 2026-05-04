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

function App() {
  const [positions, setPositions] = useState<Position[]>([]);
  const [settings, setSettings] = useState<Settings>({ warning_zone_pct: 1.0, step_pct: 0.1 });
  const [loading, setLoading] = useState(true);
  
  // New Position State
  const [newSymbol, setNewSymbol] = useState('');
  const [newDirection, setNewDirection] = useState('UP');
  const [newBetType, setNewBetType] = useState('close');
  const [isAdding, setIsAdding] = useState(false);

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

  const deletePosition = async (id: number) => {
    if (!confirm("Emin misiniz?")) return;
    try {
      await fetch(`${API_BASE}/positions/${id}`, { method: 'DELETE' });
      fetchData();
    } catch (err) {
      console.error(err);
    }
  };

  if (loading) return <div className="min-h-screen flex items-center justify-center">Yükleniyor...</div>;

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
    </div>
  );
}

export default App;
