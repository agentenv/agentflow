import { useEffect, useState } from 'react';
import type { FC } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import { fetchScratchboard } from './api';
import { RefreshCw } from 'lucide-react';

interface ScratchboardProps {
  runId: string | null;
}

export const Scratchboard: FC<ScratchboardProps> = ({ runId }) => {
  const [content, setContent] = useState<string>('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!runId) {
      setContent('');
      return;
    }

    let isMounted = true;
    const loadBoard = async () => {
      setLoading(true);
      try {
        const text = await fetchScratchboard(runId);
        if (isMounted) setContent(text || 'No shared context found.');
      } catch (err) {
        if (isMounted) setContent('Error fetching scratchboard.');
      }
      if (isMounted) setLoading(false);
    };

    loadBoard();
    const timer = setInterval(loadBoard, 5000);
    return () => {
      isMounted = false;
      clearInterval(timer);
    };
  }, [runId]);

  if (!runId) {
    return (
      <div className="h-full border-l border-slate-200 bg-white flex flex-col shrink-0">
        <div className="p-4 border-b border-slate-200">
          <h2 className="font-semibold text-slate-800 tracking-wide text-sm">SCRATCHBOARD</h2>
        </div>
        <div className="p-4 text-sm text-slate-500">No active run selected.</div>
      </div>
    );
  }

  return (
    <div className="h-full border-l border-slate-200 bg-white flex flex-col shrink-0">
      <div className="p-4 border-b border-slate-200 flex items-center justify-between">
        <h2 className="font-semibold text-slate-800 tracking-wide text-sm flex items-center gap-2">
          SCRATCHBOARD
          {loading && <RefreshCw className="w-3 h-3 animate-spin text-slate-400" />}
        </h2>
      </div>
      <div className="flex-1 overflow-y-auto p-4 bg-slate-50">
        <div className="prose prose-sm prose-slate max-w-none">
          <ReactMarkdown 
            remarkPlugins={[remarkGfm]} 
            rehypePlugins={[rehypeHighlight]}
          >
            {content}
          </ReactMarkdown>
        </div>
      </div>
    </div>
  );
};
