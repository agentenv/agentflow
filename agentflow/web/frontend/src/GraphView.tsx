import React, { useMemo, useEffect } from 'react';
import { ReactFlow, Background, Controls, MarkerType, ReactFlowProvider, useReactFlow, Position } from '@xyflow/react';
import type { Node, Edge } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import type { Run } from './api';

interface GraphViewProps {
  run: Run | null;
  onSelectNode: (nodeId: string) => void;
  sidebarWidth?: number;
  rightPanelWidth?: number;
}

const statusColor = (status?: string) => {
  switch (status) {
    case 'completed': return '#1a7f37';
    case 'failed':
    case 'cancelled': return '#cf222e';
    case 'running':
    case 'retrying': return '#d29922';
    default: return '#57606a';
  }
};

const statusBorder = (status?: string) => {
  switch (status) {
    case 'completed': return '#dafbe1';
    case 'failed':
    case 'cancelled': return '#ffebe9';
    case 'running':
    case 'retrying': return '#fff8c5';
    default: return '#eaeef2';
  }
};

const GraphContent: React.FC<GraphViewProps> = ({ run, onSelectNode, sidebarWidth, rightPanelWidth }) => {
  const { fitView } = useReactFlow();
  
  const nodes: Node[] = useMemo(() => {
    if (!run?.pipeline?.nodes) return [];
    // ... (rest of node logic)
    
    // Very simple layout horizontally by levels
    const levels: Record<string, number> = {};
    const visiting = new Set<string>();
    
    const visit = (nodeId: string): number => {
      if (levels[nodeId] !== undefined) return levels[nodeId];
      if (visiting.has(nodeId)) return 0;
      visiting.add(nodeId);
      const nodeSpec = run.pipeline.nodes.find(n => n.id === nodeId);
      const deps = nodeSpec?.depends_on || [];
      const depLevels = deps.map(d => visit(d));
      visiting.delete(nodeId);
      levels[nodeId] = depLevels.length ? Math.max(...depLevels) + 1 : 0;
      return levels[nodeId];
    };

    run.pipeline.nodes.forEach(n => visit(n.id));

    const levelCounts: Record<number, number> = {};
    
    return run.pipeline.nodes.map(n => {
      const level = levels[n.id] || 0;
      const count = levelCounts[level] || 0;
      levelCounts[level] = count + 1;
      
      const nodeState = run.nodes?.[n.id];
      const status = nodeState?.status || 'waiting';
      const bgColor = statusBorder(status);
      const textColor = statusColor(status);

      return {
        id: n.id,
        position: { x: level * 280 + 50, y: count * 140 + 50 },
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        data: { 
          label: (
            <div className="flex flex-col gap-1 items-start text-left p-1 w-40 overflow-hidden text-sm">
              <strong className="truncate font-semibold text-slate-800">{n.id}</strong>
              <span style={{ color: textColor }} className="text-xs uppercase font-bold tracking-wider">{status}</span>
              <span className="text-[10px] text-slate-500">{n.agent || n.model || n.kind}</span>
            </div>
          ) 
        },
        style: {
          background: bgColor,
          borderColor: textColor,
          borderWidth: 2,
          borderRadius: 8,
          boxShadow: '0 2px 4px rgba(0,0,0,0.05)',
        }
      };
    });
  }, [run]);

  const edges: Edge[] = useMemo(() => {
    if (!run?.pipeline?.nodes) return [];
    
    const edgeMap = new Map<string, Edge>();
    
    run.pipeline.nodes.forEach(n => {
      if (n.depends_on) {
        n.depends_on.forEach(depId => {
          const edgeId = `e-${depId}-${n.id}`;
          if (!edgeMap.has(edgeId)) {
            edgeMap.set(edgeId, {
              id: edgeId,
              source: depId,
              target: n.id,
              type: 'smoothstep',
              markerEnd: { type: MarkerType.ArrowClosed },
              style: { stroke: '#94a3b8', strokeWidth: 1.5 },
            });
          }
        });
      }
    });
    
    return Array.from(edgeMap.values());
  }, [run]);


  // Automatically fit view when container dimensions change or new nodes are added
  useEffect(() => {
    // Only fit view when sidebars move or the number of nodes changes. 
    // Do NOT fit view just because a node status (color) changed.
    fitView({ duration: 0 });
  }, [nodes.length, fitView, sidebarWidth, rightPanelWidth]);

  if (!run) return <div className="flex-1 flex items-center justify-center text-slate-400 font-bold bg-slate-50 uppercase tracking-widest text-[10px]">No Run Selected</div>;

  return (
    <div className="flex-1 flex flex-col min-w-0 min-h-0 relative">
      <div className="h-full w-full bg-slate-50 relative">
        <ReactFlow 
          nodes={nodes} 
          edges={edges} 
          onNodeClick={(_: React.MouseEvent, node: Node) => onSelectNode(node.id)}
          fitView
          minZoom={0.2}
          maxZoom={1.5}
          fitViewOptions={{ padding: 0.2 }}
        >
          <Background color="#cbd5e1" gap={16} />
          <Controls />
        </ReactFlow>
      </div>
    </div>
  );
};

export const GraphView: React.FC<GraphViewProps> = (props) => (
  <ReactFlowProvider>
    <GraphContent {...props} />
  </ReactFlowProvider>
);
