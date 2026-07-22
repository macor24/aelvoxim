import { useState, useRef, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Send } from 'lucide-react';
import { useSessionStore } from '../../stores/sessionStore';

interface Props {
  onSend: (content: string) => void;
  disabled?: boolean;
}

export default function MessageInput({ onSend, disabled }: Props) {
  const { t } = useTranslation();
  const [input, setInput] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const activeId = useSessionStore((s) => s.activeSessionId);

  // Focus on mount, when disabled clears, and when session changes
  useEffect(() => {
    if (!disabled) {
      const timer = setTimeout(() => textareaRef.current?.focus(), 50);
      return () => clearTimeout(timer);
    }
  }, [disabled, activeId]);

  const handleSend = () => {
    if (!input.trim() || disabled) return;
    onSend(input.trim());
    setInput('');
  };

  return (
    <div className="p-3 bg-white/80 dark:bg-gray-950/80 backdrop-blur-sm border-t border-gray-200 dark:border-gray-800">
      <div className="flex items-end gap-2 max-w-4xl mx-auto px-2 md:px-8">
        <div className="flex-1 relative">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { 
              if (e.key === 'Enter' && !e.shiftKey) { 
                e.preventDefault(); 
                handleSend(); 
              } 
            }}
            placeholder={t('chat.placeholder')}
            rows={2}
            className="w-full resize-none rounded-2xl border-0 bg-gray-100 dark:bg-gray-800 px-4 py-3 text-[15px] outline-none focus:ring-2 focus:ring-brand-500/50 focus:bg-white dark:focus:bg-gray-900 shadow-soft focus:shadow-soft-lg transition-all duration-200"
            style={{ minHeight: '52px', maxHeight: '200px' }}
            disabled={disabled}
          />
        </div>
        
        {/* 发送按钮 — 渐变 */}
        <button
          onClick={handleSend}
          disabled={!input.trim() || disabled}
          className="p-3 rounded-2xl bg-gradient-brand text-white shadow-soft hover:shadow-soft-lg hover:scale-105 active:scale-95 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:scale-100 transition-all duration-200"
        >
          <Send size={20} strokeWidth={2} />
        </button>
      </div>
    </div>
  );
}
