import React, { useState } from 'react';
import { Search, Clock, Zap, Layers, Calendar, Activity, CheckCircle, XCircle } from 'lucide-react';
import type { Run } from './api';

interface SidebarProps {
  runs: Run[];
  activeRunId: string | null;
  onSelectRun: (id: string) => void;
  onRefresh: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({ runs, activeRunId, onSelectRun, onRefresh }) => {
  const [searchQuery, setSearchQuery] = useState('');

  const formatRelativeTime = (dateStr?: string) => {
    if (!dateStr) return '-';
    const date = new Date(dateStr);
    const now = new Date();
    const diff = Math.floor((now.getTime() - date.getTime()) / 1000);
    
    if (diff < 60) return `just now`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return date.toLocaleDateString();
  };

  const calculateDuration = (run: Run) => {
    const start = run.started_at || run.created_at;
    const end = run.finished_at;
    if (!start) return '-';
    
    const startTime = new Date(start).getTime();
    const isTerminal = ['completed', 'failed', 'cancelled', 'cancelling'].includes(run.status);
    
    let endTime;
    if (isTerminal) {
      endTime = end ? new Date(end).getTime() : startTime;
    } else {
      endTime = Date.now();
    }
    
    const diff = Math.max(0, Math.floor((endTime - startTime) / 1000));
    
    if (diff < 60) return `${diff}s`;
    return `${Math.floor(diff / 60)}m ${diff % 60}s`;
  };

  const renderStatusIcon = (status: string) => {
    switch (status) {
      case 'completed': return <CheckCircle className="w-3.5 h-3.5 text-emerald-500" />;
      case 'failed': return <XCircle className="w-3.5 h-3.5 text-red-500" />;
      case 'cancelled': return <XCircle className="w-3.5 h-3.5 text-slate-400" />;
      case 'cancelling': return <Activity className="w-3.5 h-3.5 text-amber-500 animate-pulse" />;
      case 'running': return <Activity className="w-3.5 h-3.5 text-blue-500 animate-pulse" />;
      case 'pending':
      case 'queued':
      default: return <Clock className="w-3.5 h-3.5 text-slate-400" />;
    }
  };

  return (
    <div className="w-full flex flex-col h-full bg-slate-50 border-r border-slate-200 shrink-0 select-none">
      <div className="p-4 border-b border-slate-200 bg-white shadow-sm">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500 flex items-center gap-2">
            <Zap className="w-3 h-3 text-blue-500 fill-blue-500" />
            Runs history
          </h2>
          <button 
            onClick={onRefresh}
            className="p-1 px-2 hover:bg-slate-100 rounded text-[9px] font-black text-blue-600 transition-colors uppercase"
          >
            Refresh
          </button>
        </div>
        <div className="relative group">
          <Search className="absolute left-3 top-2.5 h-3.5 w-3.5 text-slate-400 group-focus-within:text-blue-500 transition-colors" />
          <input
            type="text"
            placeholder="Search runs (id, name, status)..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-9 pr-4 py-2 bg-slate-50 border border-slate-200 rounded-lg text-xs focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all font-medium"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-2.5 space-y-1.5 custom-scrollbar">
        {runs.filter(run => {
          if (!searchQuery.trim()) return true;
          const q = searchQuery.toLowerCase();
          const nameStr = (run.pipeline?.name || 'Untitled Pipeline').toLowerCase();
          const idStr = run.id.toLowerCase();
          const statusStr = run.status.toLowerCase();
          return nameStr.includes(q) || idStr.includes(q) || statusStr.includes(q);
        }).map((run) => (
          <button
            key={run.id}
            onClick={() => onSelectRun(run.id)}
            className={`w-full text-left p-3 rounded-xl transition-all border outline-none group relative overflow-hidden ${
              activeRunId === run.id
                ? 'bg-white border-blue-200 shadow-md ring-1 ring-blue-100'
                : 'hover:bg-white/60 border-transparent hover:border-slate-200 shadow-none'
            }`}
          >
            {activeRunId === run.id && (
               <div className="absolute left-0 top-0 bottom-0 w-1 bg-blue-500" />
            )}
            
            <div className="flex items-center justify-between mb-1.5 min-w-0">
               <div className="flex items-center gap-2 min-w-0">
                  {renderStatusIcon(run.status)}
                  <div className="flex flex-col">
                    <span className={`font-black tracking-tight text-[12px] truncate ${activeRunId === run.id ? 'text-slate-900' : 'text-slate-600 group-hover:text-slate-900'}`}>
                      {run.pipeline?.name || 'Untitled Pipeline'}
                    </span>
                    {(run.status === 'cancelling' || run.status === 'cancelled') && (
                      <span className={`text-[9px] font-black uppercase tracking-widest ${run.status === 'cancelling' ? 'text-amber-500 animate-pulse' : 'text-red-500'}`}>
                        {run.status}
                      </span>
                    )}
                  </div>
               </div>
               <span className="text-[9px] font-mono text-slate-400 shrink-0 bg-slate-100 px-1.5 py-0.5 rounded">
                 {run.id.slice(0, 8)}
               </span>
            </div>

            <div className="flex flex-col gap-1 px-5">
               <div className="flex items-center gap-3 text-[9px] font-bold text-slate-400">
                  <div className="flex items-center gap-1">
                    <Calendar size={10} className="text-slate-300" />
                    {formatRelativeTime(run.created_at)}
                  </div>
                  <div className="flex items-center gap-1">
                    <Clock size={10} className="text-slate-300" />
                    {calculateDuration(run)}
                  </div>
                  <div className="flex items-center gap-1">
                    <Layers size={10} className="text-slate-300" />
                    {run.pipeline?.nodes?.length || 0} nodes
                  </div>
               </div>
            </div>
          </button>
        ))}
        {runs.filter(run => {
          if (!searchQuery.trim()) return true;
          const q = searchQuery.toLowerCase();
          return (run.pipeline?.name || 'Untitled Pipeline').toLowerCase().includes(q) || run.id.toLowerCase().includes(q) || run.status.toLowerCase().includes(q);
        }).length === 0 && (
          <div className="p-8 text-center">
            <Activity className="w-8 h-8 text-slate-200 mx-auto mb-2" />
            <p className="text-[10px] text-slate-400 font-bold uppercase tracking-widest">No runs found</p>
          </div>
        )}
      </div>
    </div>
  );
};
