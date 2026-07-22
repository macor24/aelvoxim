export interface Session {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  isBackend?: boolean;
}

export type MessageRole = 'user' | 'assistant' | 'system';
export type MessageStatus = 'sending' | 'streaming' | 'done' | 'error';

export interface Message {
  id: string;
  role: MessageRole;
  content: string;
  timestamp: string;
  status: MessageStatus;
}
