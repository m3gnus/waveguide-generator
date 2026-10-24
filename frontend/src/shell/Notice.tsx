import type { ReactNode } from 'react';

export function Notice({ tone = 'info', title, children, actions, className = '' }: {
  tone?: 'info' | 'warning' | 'error';
  title?: ReactNode;
  children: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return <div className={`notice notice-${tone}${className ? ` ${className}` : ''}`} role={tone === 'error' ? 'alert' : 'status'}>
    {title && <><div className="notice-title">{title}</div>{'\n'}</>}
    <div className="notice-detail">{children}</div>
    {actions && <>{'\n'}<div className="notice-actions">{actions}</div></>}
  </div>;
}
