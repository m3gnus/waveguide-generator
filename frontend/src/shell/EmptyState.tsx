import type { ReactElement, ReactNode } from 'react';

export function EmptyState({ title, detail, action, className = '' }: {
  title: ReactNode;
  detail?: ReactNode;
  action?: ReactElement;
  className?: string;
}) {
  return <div className={`empty-state${className ? ` ${className}` : ''}`} role="status">
    <b>{title}</b>
    {detail && <>{'\n'}<div className="empty-state-detail">{detail}</div></>}
    {action && <>{'\n'}<div className="empty-state-action">{action}</div></>}
  </div>;
}
