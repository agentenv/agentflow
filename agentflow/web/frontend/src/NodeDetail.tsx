import { useState, useRef, useEffect } from 'react';
import type { FC } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import type { NodeState } from './api';
import { Terminal, Brain, FileText, AlertCircle, ChevronDown, Copy, Zap, CheckCircle, PlayCircle, Hash, Clock, RefreshCw, Database, Activity } from 'lucide-react';
import { fetchArtifactContent } from './api';
import { Scratchboard } from './Scratchboard';

interface NodeDetailProps {
  runId: string | null;
  nodeId: string | null;
  nodeState: NodeState | null;
  agentKind?: string;
}

export const NodeDetail: FC<NodeDetailProps> = ({ runId, nodeId, nodeState }) => {
  const [activeTab, setActiveTab ] = useState<'output' | 'thinking' | 'stdout' | 'stderr' | 'scratchboard' | 'config'>('output');
  const [showRawTrace, setShowRawTrace] = useState(false);
  const [configContent, setConfigContent] = useState<string | null>(null);
  const [configLoading, setConfigLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const [logs, setLogs] = useState<string | null>(null);
  const [logsLoading, setLogsLoading] = useState(false);

  // Auto-tab selection logic based on output and status
  const lastNodeId = useRef<string | null>(null);
  const lastStatus = useRef(nodeState?.status);

  useEffect(() => {
    if (!nodeId) return;

    const isNodeChanged = lastNodeId.current !== nodeId;
    const isTerminal = ['completed', 'failed', 'cancelled'].includes(nodeState?.status || '');
    
    // Initial selection ONLY when clicking a NEW node
    if (isNodeChanged) {
      if (nodeState?.output) {
        setActiveTab('output');
      } else if (!isTerminal || nodeState?.status === 'running' || nodeState?.status === 'retrying') {
        setActiveTab('stdout');
      }
      lastNodeId.current = nodeId;
    }

    // Auto-switch from stdout/stderr to output when node FINISHES
    const transitionedToFinished = 
      !['completed', 'failed', 'cancelled'].includes(lastStatus.current || '') && 
      isTerminal;

    if (transitionedToFinished && (activeTab === 'stdout' || activeTab === 'stderr') && nodeState?.output) {
      setActiveTab('output');
    }

    lastStatus.current = nodeState?.status;
  }, [nodeId, nodeState?.status, !!nodeState?.output]);

  // Auto-scroll to bottom logic
  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [nodeState?.output, nodeState?.trace_events, logs, activeTab, autoScroll]);

  // Fetch stdout/stderr/config logs with auto-refresh for active nodes
  useEffect(() => {
    if (!runId || !nodeId) return;

    const isTerminal = ['completed', 'failed', 'cancelled'].includes(nodeState?.status || '');
    
    const fetchLogs = async () => {
      if (activeTab === 'stdout' || activeTab === 'stderr') {
        if (!logs) setLogsLoading(true);
        try {
          const fileName = activeTab === 'stdout' ? 'stdout.log' : 'stderr.log';
          const content = await fetchArtifactContent(runId, nodeId, fileName);
          setLogs(content);
        } catch (err) {
          setLogs(`Log not found or empty.`);
        } finally {
          setLogsLoading(false);
        }
      } else if (activeTab === 'config') {
        if (!configContent) setConfigLoading(true);
        try {
          const content = await fetchArtifactContent(runId, nodeId, 'launch.json');
          setConfigContent(content);
        } catch (err) {
          setConfigContent(`Configuration not found.`);
        } finally {
          setConfigLoading(false);
        }
      } else {
        setLogs(null);
      }
    };

    fetchLogs();
    
    if (!isTerminal && (activeTab === 'stdout' || activeTab === 'stderr')) {
      const timer = setInterval(fetchLogs, 1000);
      return () => clearInterval(timer);
    }
  }, [activeTab, runId, nodeId, nodeState?.status]);

  if (!nodeId) {
    return (
      <div className="w-full bg-white border-l border-slate-200 h-full flex flex-col p-8 text-center justify-center text-slate-400">
        <p>Select a node to see details</p>
      </div>
    );
  }

  const getEventStyle = (type: string) => {
     const types: Record<string, { icon: any, color: string, bg: string }> = {
       'thread.started': { icon: <PlayCircle size={14} />, color: 'text-blue-500', bg: 'bg-blue-50' },
       'turn.started': { icon: <RefreshCw size={14} />, color: 'text-indigo-500', bg: 'bg-indigo-50' },
       'thought': { icon: <Brain size={14} />, color: 'text-purple-500', bg: 'bg-purple-50' },
       'action.call': { icon: <Zap size={14} />, color: 'text-amber-500', bg: 'bg-amber-50' },
       'action.response': { icon: <CheckCircle size={14} />, color: 'text-emerald-500', bg: 'bg-emerald-50' },
       'stdout': { icon: <Terminal size={14} />, color: 'text-slate-500', bg: 'bg-slate-50' },
       'stderr': { icon: <AlertCircle size={14} />, color: 'text-red-500', bg: 'bg-red-50' },
       'error': { icon: <AlertCircle size={14} />, color: 'text-red-600', bg: 'bg-red-50' },
       'default': { icon: <Hash size={14} />, color: 'text-slate-400', bg: 'bg-slate-50' }
     };
     
     // Match by kind or raw type
     if (type.includes('thought')) return types['thought'];
     if (type.includes('action.call')) return types['action.call'];
     if (type.includes('action.response')) return types['action.response'];
     if (type.includes('stdout')) return types['stdout'];
     if (type.includes('stderr')) return types['stderr'];
     
     return types[type] || types['default'];
  }

  const renderTrace = (trace: any, index: number) => {
    const rawType = trace?.raw?.type || trace?.kind || '';
    const kind = trace?.title || trace?.kind || rawType || 'event';
    const style = getEventStyle(rawType);
    let content = typeof trace?.content === 'object' ? JSON.stringify(trace.content, null, 2) : String(trace?.content || '');
    const timestamp = trace?.timestamp ? new Date(trace.timestamp).toLocaleTimeString() : '';
    
    // Check if content is empty but there's raw data
    const rawData = trace?.raw || {};
    const hasInterestingData = Object.keys(rawData).some(k => !['type', 'timestamp', 'node_id', 'agent', 'attempt'].includes(k));
    const isStartEvent = rawType.includes('started') || rawType.includes('completed');

    // Specialized rendering for Command Execution
    const isCommand = kind.toLowerCase().includes('command_execution') || rawType.toLowerCase().includes('command_execution');
    const commandText = rawData?.command || rawData?.cmd || rawData?.item?.command || rawData?.item?.cmd || (typeof rawData?.args === 'string' ? rawData.args : null);

    return (
      <div key={index} className="relative pl-6 pb-6 last:pb-2 group">
        {/* Timeline connector */}
        <div className="absolute left-[11px] top-4 bottom-0 w-px bg-slate-200 group-last:bg-transparent" />
        
        {/* Event Icon/Marker */}
        <div className={`absolute left-0 top-1 w-6 h-6 rounded-full ${style.bg} ${style.color} flex items-center justify-center ring-4 ring-white z-10 shadow-sm border border-slate-100`}>
          {style.icon}
        </div>

        <div className="flex flex-col">
          {!isCommand && (
            <div className="flex items-center justify-between gap-4 mb-2">
              <span className={`text-[10px] font-extrabold uppercase tracking-widest ${style.color} flex items-center gap-1.5`}>
                {kind.replace(/\./g, ' ')}
              </span>
              <span className="text-[10px] text-slate-400 font-mono whitespace-nowrap bg-white px-2 rounded-full border border-slate-100 shadow-sm opacity-0 group-hover:opacity-100 transition-opacity">
                 {timestamp}
              </span>
            </div>
          )}

          <div className="space-y-2">
            {isCommand && commandText && (
               <div className="bg-slate-900 rounded-xl p-3 border border-slate-800 shadow-lg group/cmd relative overflow-hidden">
                 <div className="flex items-center justify-between mb-1.5">
                    <div className="flex gap-1.5">
                      <div className="w-1.5 h-1.5 rounded-full bg-red-500/60" />
                      <div className="w-1.5 h-1.5 rounded-full bg-amber-500/60" />
                      <div className="w-1.5 h-1.5 rounded-full bg-emerald-500/60" />
                    </div>
                    {timestamp && <span className="text-[9px] text-slate-500 font-mono">{timestamp}</span>}
                    <button 
                      onClick={() => navigator.clipboard.writeText(commandText)}
                      className="text-slate-500 hover:text-white transition-colors p-1"
                    >
                      <Copy size={10} />
                    </button>
                 </div>
                 <code className="text-[11px] font-mono text-emerald-400 block whitespace-pre-wrap break-all leading-relaxed">
                    $ {commandText}
                 </code>
               </div>
            )}

            {content && content.trim() && !isCommand && (
              <div className="text-xs text-slate-600 bg-white p-3.5 rounded-xl border border-slate-200/60 leading-relaxed font-sans shadow-sm prose prose-sm prose-slate max-w-none prose-pre:bg-slate-900 prose-pre:text-emerald-400 prose-code:text-rose-500 prose-code:bg-rose-50 prose-code:px-1 prose-code:rounded">
                <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
                  {content}
                </ReactMarkdown>
              </div>
            )}
            
            {(hasInterestingData && !isCommand && (!content.trim() || !isStartEvent)) && (
              <pre className="text-[10px] bg-slate-900/95 text-slate-300 p-3 rounded-xl border border-slate-800 overflow-x-auto font-mono shadow-inner max-h-80 custom-scrollbar whitespace-pre-wrap">
                {JSON.stringify(rawData, null, 2)}
              </pre>
            )}
          </div>
        </div>
      </div>
    );
  };

  const renderLogContent = (content: string) => {
    if (!content) return null;
    
    const lines = content.split('\n').filter(l => l.trim());
    return (
      <div className="space-y-3 font-mono text-[11px]">
        {lines.map((line, i) => {
          let parsed: any = null;
          // Try to find JSON in the line (sometimes logs have prefixes)
          const jsonMatch = line.match(/\{.*\}/);
          if (jsonMatch) {
            try {
               parsed = JSON.parse(jsonMatch[0]);
            } catch (e) {}
          }

          if (parsed) {
             const type = (parsed.type || parsed.kind || 'LOG').toUpperCase();
             const isError = type.includes('ERROR') || type.includes('STDERR');
             const timestamp = parsed.timestamp ? new Date(parsed.timestamp).toLocaleTimeString() : '';
             
             return (
               <div key={i} className={`p-0 rounded-xl border shadow-sm overflow-hidden ${isError ? 'bg-red-50/30 border-red-100' : 'bg-slate-50/50 border-slate-200/60'}`}>
                 <div className={`px-3 py-1.5 flex items-center justify-between border-b ${isError ? 'bg-red-50 border-red-100' : 'bg-slate-100 border-slate-200/60'}`}>
                   <span className={`font-black px-1.5 py-0.5 rounded text-[9px] ${isError ? 'bg-red-500 text-white' : 'bg-slate-600 text-white'}`}>
                     {type}
                   </span>
                   {timestamp && <span className="text-[9px] text-slate-400 font-bold">{timestamp}</span>}
                 </div>
                 <div className="p-3">
                   <pre className={`whitespace-pre-wrap leading-relaxed ${isError ? 'text-red-700' : 'text-slate-700'}`}>
                     {typeof parsed.data === 'object' ? JSON.stringify(parsed.data, null, 2) : (parsed.content || JSON.stringify(parsed, null, 2))}
                   </pre>
                 </div>
               </div>
             );
          }

          return (
            <div key={i} className="p-2 px-3 text-slate-500 border-l-2 border-slate-200 bg-white rounded-r-lg shadow-sm">
              {line}
            </div>
          );
        })}
      </div>
    );
  }

  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const target = e.currentTarget;
    const isAtBottom = Math.abs(target.scrollHeight - target.clientHeight - target.scrollTop) < 20;
    if (autoScroll !== isAtBottom) {
      setAutoScroll(isAtBottom);
    }
  };

  

  return (
    <div className="w-full bg-white border-l border-slate-200 h-full flex flex-col shrink-0 overflow-hidden relative shadow-2xl z-20">
      <div className="p-4 border-b border-slate-200 bg-white/80 backdrop-blur-md sticky top-0 z-30">
        <div className="flex items-center justify-between mb-2">
           <h2 className="font-bold text-slate-900 text-base truncate flex items-center gap-2">
             <div className="w-2 h-2 rounded-full bg-blue-500" />
             {nodeId}
           </h2>
           <span className={`text-[10px] px-2 py-0.5 rounded-full font-black uppercase shadow-sm border ${
             nodeState?.status === 'completed' ? 'bg-green-50 text-green-700 border-green-200' :
             nodeState?.status === 'failed' ? 'bg-red-50 text-red-700 border-red-200' :
             nodeState?.status === 'running' ? 'bg-blue-50 text-blue-700 border-blue-200' :
             nodeState?.status === 'cancelling' ? 'bg-amber-50 text-amber-700 border-amber-200' :
             nodeState?.status === 'cancelled' ? 'bg-slate-100 text-slate-500 border-slate-300' :
             'bg-slate-50 text-slate-600 border-slate-200'
           }`}>
             {nodeState?.status || 'waiting'}
           </span>
        </div>
        <div className="flex flex-col gap-2.5">
          <div className="flex items-center justify-between text-[10px] text-slate-400 font-bold uppercase tracking-wider">
            <span className="flex items-center gap-1"><Clock size={10} /> Attempt {nodeState?.current_attempt || 0}</span>
          </div>
        </div>
      </div>

      <div className="flex border-b border-slate-200 bg-slate-50/50 p-1 shrink-0 gap-1 overflow-x-auto no-scrollbar scroll-smooth">
        {[
          { id: 'output', icon: <FileText size={13} />, label: 'Output' },
          { id: 'thinking', icon: <Brain size={13} />, label: 'Thinking' },
          { id: 'stdout', icon: <Terminal size={13} />, label: 'Stdout' },
          { id: 'stderr', icon: <AlertCircle size={13} />, label: 'Stderr' },
          { id: 'scratchboard', icon: <Activity size={13} />, label: 'Scratchboard' },
          { id: 'config', icon: <Hash size={13} />, label: 'Config' },
        ].map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id as any)}
            className={`flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-bold rounded-md transition-all whitespace-nowrap ${
              activeTab === tab.id 
                ? 'bg-white text-blue-600 shadow-sm border border-slate-200 ring-1 ring-black/5' 
                : 'text-slate-500 hover:text-slate-700 hover:bg-slate-200/50'
            }`}
          >
            {tab.icon}
            {tab.label}
          </button>
        ))}
      </div>

      <div 
        ref={scrollRef}
        onScroll={['output', 'thinking', 'stdout', 'stderr'].includes(activeTab) ? handleScroll : undefined}
        className="flex-1 overflow-y-auto custom-scrollbar bg-white/40 relative"
      >
        <div className="p-4 min-h-full">
          {activeTab === 'output' && (
            <div className="prose prose-sm prose-slate max-w-none relative">
              {nodeState?.output ? (
                 <ReactMarkdown 
                   remarkPlugins={[remarkGfm]} 
                   rehypePlugins={[rehypeHighlight]}
                 >
                   {typeof nodeState.output === 'string' ? nodeState.output : JSON.stringify(nodeState.output, null, 2)}
                 </ReactMarkdown>
              ) : (
                 <div className="flex flex-col items-center justify-center py-12 text-slate-300 gap-3">
                   <div className="w-12 h-12 rounded-full border-2 border-slate-100 flex items-center justify-center animate-spin">
                      <RefreshCw size={20} />
                   </div>
                   <p className="italic text-xs font-medium">Wait for result...</p>
                 </div>
              )}
            </div>
          )}

          {activeTab === 'thinking' && (
            <div className="space-y-0">
              <div className="flex justify-between items-center mb-4 px-1">
                 <h3 className="text-[10px] font-black uppercase tracking-widest text-slate-400">Timeline</h3>
                 <button 
                   onClick={() => setShowRawTrace(!showRawTrace)}
                   className={`text-[9px] font-black px-2 py-1 rounded transition-all flex items-center gap-1.5 ${
                      showRawTrace ? 'bg-slate-900 text-white shadow-lg' : 'bg-slate-100 text-slate-500 hover:bg-slate-200'
                   }`}
                 >
                   <Database size={10} />
                   {showRawTrace ? 'VIEW VISUAL' : 'VIEW RAW JSON'}
                 </button>
              </div>
              {showRawTrace ? (
                 <pre className="text-[10px] bg-slate-900 text-emerald-400 p-4 rounded-xl border border-slate-800 font-mono shadow-inner overflow-x-auto whitespace-pre-wrap leading-relaxed">
                   {JSON.stringify(nodeState?.trace_events || [], null, 2)}
                 </pre>
              ) : (
                (nodeState?.trace_events || []).length > 0 ? (
                  nodeState?.trace_events?.map((e, idx) => renderTrace(e, idx))
                ) : (
                  <div className="flex flex-col items-center justify-center py-12 text-slate-300 gap-3 border-2 border-dashed border-slate-100 rounded-2xl">
                    <Brain size={32} strokeWidth={1} />
                    <p className="italic text-xs font-medium">Thinking process not yet logged...</p>
                  </div>
                )
              )}
            </div>
          )}

          {activeTab === 'scratchboard' && (
             <div className="py-2">
                <Scratchboard runId={runId} />
             </div>
          )}

          {activeTab === 'config' && (
             <div className="">
                <div className="flex items-center gap-2 mb-4 text-slate-400 px-1">
                  <Hash size={12} className="text-blue-500" />
                  <span className="text-[10px] font-black uppercase tracking-widest">Configuration (launch.json)</span>
                </div>
                {configLoading ? (
                  <div className="flex flex-col items-center justify-center py-12 text-slate-300 gap-3">
                    <RefreshCw className="animate-spin" size={20} />
                    <p className="text-xs">Fetching config...</p>
                  </div>
                ) : (
                  <pre className="text-[11px] bg-slate-900 text-slate-100 p-5 rounded-2xl border border-slate-800 font-mono shadow-2xl overflow-x-auto whitespace-pre-wrap leading-relaxed">
                    {configContent || 'No configuration available.'}
                  </pre>
                )}
             </div>
          )}

          {(activeTab === 'stdout' || activeTab === 'stderr') && (
             <div className="">
               {logsLoading ? (
                 <div className="flex flex-col items-center justify-center py-12 text-slate-300 gap-3">
                   <RefreshCw className="animate-spin" size={20} />
                   <p className="text-xs">Fetching logs...</p>
                 </div>
               ) : renderLogContent(logs || '')}
             </div>
          )}
        </div>
      </div>

      {['output', 'thinking', 'stdout', 'stderr'].includes(activeTab) && !autoScroll && (
        <button 
          onClick={() => {
            setAutoScroll(true);
            if (scrollRef.current) {
               scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
            }
          }}
          className="absolute bottom-6 right-6 bg-blue-600/90 backdrop-blur-sm text-white px-4 py-2.5 rounded-full shadow-2xl shadow-blue-500/40 hover:bg-blue-700 transition-all hover:scale-105 active:scale-95 ring-4 ring-white flex items-center gap-2 font-black text-[10px] uppercase tracking-widest z-50 group"
        >
          <span className="opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap">Latest</span>
          <ChevronDown size={14} className="" />
        </button>
      )}
    </div>
  );
};
