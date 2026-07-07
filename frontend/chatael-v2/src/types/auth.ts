export interface Tenant {
  id: string;
  name: string;
  email?: string;
  apiKey: string;
  apiUrl: string;
  deepseekKey?: string;
  deepseekModel?: string;
}
