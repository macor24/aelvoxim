import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Copy, Check, Trash2, Loader2 } from 'lucide-react';
import type { Message } from '../../types/chat';
import ConfirmDialog from '../common/ConfirmDialog';

interface Props {
  message: Message;
  onDelete: (id: string) => void;
  onRetry?: () => void;
  tenantName?: string;
}

export default function MessageItem({ message, onDelete, onRetry, tenantName = '你' }: Props) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const isUser = message.role === 'user';
  const isStreaming = message.status === 'streaming';
  const isError = message.status === 'error';

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
      return;
    } catch {}
    // HTTP fallback: create a temp textarea
    try {
      const textarea = document.createElement('textarea');
      textarea.value = message.content;
      textarea.style.position = 'fixed';
      textarea.style.opacity = '0';
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {}
  };

  return (
    <>
      <div className={`group flex flex-col mb-5 px-2 md:px-8 message-enter ${isUser ? 'items-end' : 'items-start'}`}>
        {/* AI 标识 — 气泡外上方，靠左 */}
        {!isUser && (
          <div className="flex items-center gap-2 mb-1 ml-1">
            <span className="text-[11px] text-gray-400 font-medium">Aelvoxim</span>
          </div>
        )}
        {/* 用户标识 — 气泡外上方，靠右 */}
        {isUser && (
          <div className="flex items-center gap-2 mb-1 mr-1">
            <span className="text-[11px] text-gray-400 font-medium">{tenantName}</span>
          </div>
        )}
        
        <div className={`
          relative max-w-[75%] md:max-w-[65%] px-4 py-3 text-[15px] leading-relaxed
          ${isUser 
            ? 'bg-brand-100 text-gray-900 rounded-3xl rounded-br-md shadow-sm' 
            : 'bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-100 rounded-3xl rounded-bl-md shadow-soft ml-2 md:ml-2'
          }
          ${isStreaming ? 'border border-brand-300/50 dark:border-brand-700/50' : ''}
          ${!isUser && !message.content && isStreaming ? 'ml-2' : ''}
        `}>
          {isStreaming && !message.content ? (
            /* 思考中状态 */
            <div className="flex items-center gap-2 text-gray-500 py-2">
              <Loader2 size={16} className="animate-spin" />
              <span className="text-sm">思考中<span className="loading-dots"></span></span>
            </div>
          ) : (
            <div className="whitespace-pre-wrap break-words">{message.content}</div>
          )}
          
          {/* 流式光标 */}
          {isStreaming && message.content && (
            <span className="inline-block w-[2px] h-4 bg-brand-500 ml-0.5 animate-pulse" />
          )}
          
          {/* 错误状态 */}
          {isError && (
            <span className="text-xs opacity-60 ml-1">
              ({t('message.error')})
              {onRetry && (
                <button onClick={onRetry}
                  className="ml-2 px-2 py-0.5 text-xs rounded bg-blue-500/20 text-blue-600 dark:text-blue-400 hover:bg-blue-500/30 transition-colors">
                  🔄 重试
                </button>
              )}
            </span>
          )}
          
          {/* 底部操作条 */}
          <div className={`
            absolute -bottom-7 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity
            ${isUser ? 'right-0' : 'left-0'}
          `}>
            <button 
              onClick={handleCopy} 
              className="p-1 rounded bg-gray-100 dark:bg-gray-700 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition-colors" 
              title={t('message.copy')}
            >
              {copied ? <Check size={11} /> : <Copy size={11} />}
            </button>
            <button 
              onClick={() => setShowConfirm(true)} 
              className="p-1 rounded bg-gray-100 dark:bg-gray-700 text-gray-400 hover:text-red-500 transition-colors" 
              title={t('message.delete')}
            >
              <Trash2 size={11} />
            </button>
          </div>
        </div>
      </div>
      
      <ConfirmDialog 
        open={showConfirm} 
        message={t('message.confirmDelete')}
        onConfirm={() => { onDelete(message.id); setShowConfirm(false); }} 
        onCancel={() => setShowConfirm(false)} 
      />
    </>
  );
}
