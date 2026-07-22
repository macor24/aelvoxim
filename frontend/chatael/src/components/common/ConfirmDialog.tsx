import { useTranslation } from 'react-i18next';
import { X } from 'lucide-react';

interface Props {
  open: boolean;
  title?: string;
  message: string;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function ConfirmDialog({ open, title, message, onConfirm, onCancel }: Props) {
  const { t } = useTranslation();
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onCancel}>
      <div className="bg-white dark:bg-gray-800 rounded-xl p-6 w-80 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-lg font-semibold">{title || t('common.confirm')}</h3>
          <button onClick={onCancel} className="p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-700"><X size={16} /></button>
        </div>
        <p className="text-sm text-gray-600 dark:text-gray-400">{message}</p>
        <div className="mt-4 flex justify-end gap-3">
          <button onClick={onCancel} className="px-4 py-2 text-sm rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600">{t('common.cancel')}</button>
          <button onClick={onConfirm} className="px-4 py-2 text-sm rounded-lg bg-red-500 text-white hover:bg-red-600">{t('common.confirm')}</button>
        </div>
      </div>
    </div>
  );
}
