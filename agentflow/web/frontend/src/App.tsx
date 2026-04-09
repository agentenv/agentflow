import { useEffect, useState, useCallback, useRef } from 'react';
import type { Run, Health } from './api';
import { fetchRuns, fetchRun, cancelRun, rerunRun, fetchHealth } from './api';
import { Sidebar } from './Sidebar';
import { GraphView } from './GraphView';
import { NodeDetail } from './NodeDetail';
import { RefreshCw, Cpu, Database, Activity, Clock } from 'lucide-react';

function App() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [activeRun, setActiveRun] = useState<Run | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(() => {
    const saved = localStorage.getItem('sidebarWidth');
    return saved ? parseInt(saved, 10) : 320;
  });
  const [rightPanelWidth, setRightPanelWidth] = useState(() => {
    const saved = localStorage.getItem('rightPanelWidth');
    return saved ? parseInt(saved, 10) : 400;
  });
  const isResizingLeft = useRef(false);
  const isResizingRight = useRef(false);

  const startResizingLeft = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    isResizingLeft.current = true;
    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', stopResizing);
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  }, []);

  const startResizingRight = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    isResizingRight.current = true;
    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', stopResizing);
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  }, []);

  const stopResizing = useCallback(() => {
    isResizingLeft.current = false;
    isResizingRight.current = false;
    document.removeEventListener('mousemove', handleMouseMove);
    document.removeEventListener('mouseup', stopResizing);
    document.body.style.cursor = 'default';
    document.body.style.userSelect = 'auto';
  }, []);

  const handleMouseMove = useCallback((e: MouseEvent) => {
    if (isResizingLeft.current) {
      const newWidth = e.clientX;
      if (newWidth > 200 && newWidth < 800) {
        setSidebarWidth(newWidth);
        localStorage.setItem('sidebarWidth', newWidth.toString());
      }
    } else if (isResizingRight.current) {
      const newWidth = window.innerWidth - e.clientX;
      if (newWidth > 300 && newWidth < 900) {
        setRightPanelWidth(newWidth);
        localStorage.setItem('rightPanelWidth', newWidth.toString());
      }
    }
  }, []);

  const refreshRuns = useCallback(async () => {
     setLoading(true);
     try {
       const [data, healthData] = await Promise.all([fetchRuns(), fetchHealth()]);
       setRuns(data.sort((a, b) => new Date(b.created_at || 0).getTime() - new Date(a.created_at || 0).getTime()));
       setHealth(healthData);
     } catch (err) {
       console.error("Failed to fetch data:", err);
     } finally {
       setLoading(false);
     }
  }, []);

  const loadRun = useCallback(async (id: string) => {
     try {
       const run = await fetchRun(id);
       setActiveRun(run);
     } catch (err) {
       console.error("Failed to load run details:", err);
     }
  }, []);

  const handleCancel = async () => {
    if (!activeRunId) return;
    try {
      await cancelRun(activeRunId);
      refreshRuns();
    } catch (err) {
      console.error("Cancel failed:", err);
    }
  };

  const handleRerun = async () => {
    if (!activeRunId) return;
    try {
      const newRun = await rerunRun(activeRunId);
      setActiveRunId(newRun.id);
      refreshRuns();
    } catch (err) {
      console.error("Rerun failed:", err);
    }
  };

  // Initial run selection
  useEffect(() => {
    if (runs.length > 0 && !activeRunId) {
      setActiveRunId(runs[0].id);
    }
  }, [runs, activeRunId]);

  // Initial node selection when a run is loaded
  useEffect(() => {
    if (activeRun?.pipeline?.nodes?.length && !selectedNodeId) {
      setSelectedNodeId(activeRun.pipeline.nodes[0].id);
    }
  }, [activeRun, selectedNodeId]);

  useEffect(() => {
    refreshRuns(); // Initial fetch
    
    // Poll for both runs and health to keep the UI in sync
    const metricsTimer = setInterval(async () => {
       try {
         const [data, healthData] = await Promise.all([fetchRuns(), fetchHealth()]);
         setRuns(data.sort((a, b) => new Date(b.created_at || 0).getTime() - new Date(a.created_at || 0).getTime()));
         setHealth(healthData);
       } catch (e) {
         console.error("Polling error:", e);
       }
    }, 5000);
    
    return () => clearInterval(metricsTimer);
  }, [refreshRuns]);

  useEffect(() => {
    if (activeRunId) {
      loadRun(activeRunId);
      const timer = setInterval(() => loadRun(activeRunId), 1000);
      return () => clearInterval(timer);
    }
  }, [activeRunId, loadRun]);

  return (
    <div className="flex flex-col h-screen w-full bg-slate-900 overflow-hidden font-sans antialiased text-slate-800">
      {/* Top Header */}
      <header className="h-14 bg-slate-900 border-b border-slate-700 flex items-center px-6 shrink-0 justify-between shadow-2xl z-50">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center shadow-lg shadow-blue-500/20">
            <Cpu className="text-white w-5 h-5" />
          </div>
          <h1 className="text-white font-black tracking-tight text-xl italic uppercase">
            AgentFlow <span className="text-slate-500 font-normal ml-2 not-italic normal-case text-xs tracking-widest opacity-60">v0.1.0</span>
          </h1>
        </div>
        
        <div className="flex items-center gap-6 text-[10px] font-black uppercase tracking-[0.2em] text-slate-400">
           <div className="flex items-center gap-2.5 bg-slate-800/50 px-3 py-1.5 rounded-full border border-slate-700/50">
             <Database className="w-3.5 h-3.5 text-blue-400" />
             TOTAL: {health?.runs.total || 0}
           </div>
           <div className="flex items-center gap-2.5 bg-slate-800/50 px-3 py-1.5 rounded-full border border-slate-700/50">
             <Clock className="w-3.5 h-3.5 text-amber-400" />
             QUEUED: {health?.runs.queued || 0}
           </div>
           <div className="flex items-center gap-2.5 bg-slate-800/50 px-3 py-1.5 rounded-full border border-slate-700/50">
             <Activity className="w-3.5 h-3.5 text-emerald-400" />
             RUNNING: {health?.runs.running || 0}
           </div>

           <div className="h-6 w-px bg-slate-700/50" />

           <div className="flex gap-2">
             <button 
               onClick={handleRerun} 
               disabled={!activeRunId}
               className="px-4 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-30 text-white rounded-lg text-[10px] font-black transition-all border border-blue-400/20 shadow-lg shadow-blue-900/20 active:scale-95"
             >
               RERUN
             </button>
             <button 
               onClick={handleCancel} 
               disabled={!activeRunId || !['running', 'queued', 'pending', 'retrying'].includes(activeRun?.status || '')}
               className="px-4 py-1.5 bg-red-600 hover:bg-red-700 disabled:opacity-20 text-white rounded-lg text-[10px] font-black transition-all border border-red-500/20 shadow-lg shadow-red-900/20 active:scale-95"
             >
               STOP
             </button>
           </div>

           <button onClick={refreshRuns} disabled={loading} className="p-2 hover:bg-slate-800 rounded-full transition-colors group">
             <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin text-blue-400' : 'text-slate-400 group-hover:text-white'}`} />
           </button>
        </div>
      </header>

      {/* Main Content Area */}
      <main className="flex flex-1 overflow-hidden bg-white relative">
        <div style={{ width: sidebarWidth }} className="shrink-0 flex flex-col h-full overflow-hidden">
          <Sidebar 
             runs={runs} 
             activeRunId={activeRunId} 
             onSelectRun={(id) => { setActiveRunId(id); setSelectedNodeId(null); }} 
             onRefresh={refreshRuns} 
          />
        </div>

        {/* Left Resizer Handle */}
        <div 
          onMouseDown={startResizingLeft}
          className="w-1.5 h-full bg-slate-100 hover:bg-blue-400 cursor-col-resize transition-colors flex items-center justify-center group shrink-0"
        >
          <div className="w-px h-8 bg-slate-300 group-hover:bg-blue-300" />
        </div>

        <div className="flex-1 flex flex-col min-w-0">
           <GraphView 
             run={activeRun} 
             onSelectNode={setSelectedNodeId} 
             sidebarWidth={sidebarWidth}
             rightPanelWidth={rightPanelWidth}
           />
        </div>


        {/* Right Resizer Handle */}
        <div 
          onMouseDown={startResizingRight}
          className={`w-1.5 h-full bg-slate-100 hover:bg-blue-400 cursor-col-resize transition-colors flex items-center justify-center group shrink-0 ${selectedNodeId ? 'opacity-100' : 'opacity-0 pointer-events-none'}`}
        >
          <div className="w-px h-8 bg-slate-300 group-hover:bg-blue-300" />
        </div>

        {/* Unified Right Panel (Resizable) */}
        <div 
          className={`h-full bg-white border-l border-slate-200 shadow-2xl relative transition-all overflow-hidden shrink-0 ${selectedNodeId ? '' : 'w-0 overflow-hidden border-none'}`}
          style={selectedNodeId ? { width: rightPanelWidth } : {}}
        >
          {/* Node Detail View */}
          <div className={`absolute inset-0 transition-opacity duration-300 ${selectedNodeId ? 'opacity-100 z-20' : 'opacity-0 z-0 pointer-events-none'}`}>
            <NodeDetail 
               runId={activeRunId}
               nodeId={selectedNodeId} 
               nodeState={activeRun?.nodes?.[selectedNodeId || ''] || null} 
               agentKind={activeRun?.pipeline?.nodes?.find(n => n.id === selectedNodeId)?.agent || activeRun?.pipeline?.nodes?.find(n => n.id === selectedNodeId)?.kind}
            />
          </div>
        </div>
      </main>
    </div>
  );
}

export default App;
