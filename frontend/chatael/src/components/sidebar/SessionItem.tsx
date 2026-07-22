import { useState, useRef, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Pencil, Trash2 } from 'lucide-react';
import type { Session } from '../../types/chat';
import ConfirmDialog from '../common/ConfirmDialog';

interface Props {
  session: Session;
  isActive: boolean;
  onSelect: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  extraClass?: string;
}

export default function SessionItem({ session, isActive, onSelect, onRename, onDelete, extraClass = '' }: Props) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(session.title);
  const [showConfirm, setShowConfirm] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { if (editing) inputRef.current?.focus(); }, [editing]);

  const handleRename = () => {
    const newTitle = title.trim() || session.title;
    onRename(session.id, newTitle);
    setTitle(newTitle);
    setEditing(false);
  };

  return (
    <>
      <div
        onClick={() => onSelect(session.id)}
        className={`${extraClass}
          group relative flex items-center gap-2 px-3 py-2.5 mx-1 rounded-xl cursor-pointer
          transition-all duration-200 ease-out
          ${isActive 
            ? 'bg-brand-50 dark:bg-brand-950/50 text-brand-700 dark:text-brand-300' 
            : 'hover:bg-white dark:hover:bg-gray-900 hover:shadow-soft'
          }
          ${isActive ? 'translate-x-1' : 'hover:translate-x-0.5'}
        `}
      >
        {/* 激活指示条 */}
        {isActive && (
          <div className="absolute left-0 top-1/2 -translate-y-1/2 w-1 h-5 rounded-full bg-gradient-brand" />
        )}
        
        {editing ? (
          <input 
            ref={inputRef} 
            value={title} 
            onChange={(e) => setTitle(e.target.value)}
            onBlur={handleRename} 
            onKeyDown={(e) => { 
              if (e.key === 'Enter') handleRename(); 
              if (e.key === 'Escape') { 
                setTitle(session.title); 
                setEditing(false); 
              } 
            }}
            className="flex-1 bg-transparent border-b border-brand-400 outline-none text-sm px-1" 
            onClick={(e) => e.stopPropagation()} 
          />
        ) : (
          <span className="flex-1 truncate text-sm font-medium">{session.title}</span>
        )}
        
        {/* 操作按钮 */}
        <div className="flex gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
          <button 
            onClick={(e) => { e.stopPropagation(); setEditing(true); }} 
            className="p-1.5 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors" 
            title={t('session.rename')}
          >
            <Pencil size={13} className="text-gray-400" />
          </button>
          <button 
            onClick={(e) => { e.stopPropagation(); setShowConfirm(true); }} 
            className="p-1.5 rounded-lg hover:bg-red-100 dark:hover:bg-red-900/30 transition-colors" 
            title={t('session.delete')}
          >
            <Trash2 size={13} className="text-red-400" />
          </button>
        </div>
      </div>
      
      <ConfirmDialog 
        open={showConfirm} 
        message={t('session.confirmDelete')}
        onConfirm={() => { onDelete(session.id); setShowConfirm(false); }} 
        onCancel={() => setShowConfirm(false)} 
      />
    </>
  );
}
