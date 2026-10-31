import type { HTMLAttributes, ReactElement, ReactNode } from 'react';

export function EmptyState({ title, detail, action, className = '', children, ...attributes }: {
  title: ReactNode;
  detail?: ReactNode;
  action?: ReactElement;
  className?: string;
  children?: ReactNode;
} & Omit<HTMLAttributes<HTMLDivElement>, 'title' | 'children'>) {
  return <div className={`empty-state${className ? ` ${className}` : ''}`} role="status" {...attributes}>
    {children}
    <b>{title}</b>
    {detail && <>{'\n'}<div className="empty-state-detail">{detail}</div></>}
    {action && <>{'\n'}<div className="empty-state-action">{action}</div></>}
  </div>;
}
