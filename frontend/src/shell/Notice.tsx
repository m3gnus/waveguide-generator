import type { HTMLAttributes, ReactNode } from 'react';

export function Notice({ tone = 'info', title, children, actions, className = '', role, ...attributes }: {
  role?: HTMLAttributes<HTMLDivElement>['role'] | null;
  tone?: 'info' | 'warning' | 'error';
  title?: ReactNode;
  children: ReactNode;
  actions?: ReactNode;
  className?: string;
} & Omit<HTMLAttributes<HTMLDivElement>, 'title' | 'children' | 'role'>) {
  return <div className={`notice notice-${tone}${className ? ` ${className}` : ''}`} role={role === null ? undefined : role ?? (tone === 'error' ? 'alert' : 'status')} {...attributes}>
    {title && <><div className="notice-title">{title}</div>{'\n'}</>}
    <div className="notice-detail">{children}</div>
    {actions && <>{'\n'}<div className="notice-actions">{actions}</div></>}
  </div>;
}
