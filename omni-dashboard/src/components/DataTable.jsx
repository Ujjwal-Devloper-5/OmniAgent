import React from 'react';
import EmptyState from './EmptyState';
import Skeleton from './Skeleton';

export default function DataTable({ columns, data, loading, emptyMessage, onRowClick }) {
  if (loading) {
    return (
      <div className="w-full border border-border rounded-lg overflow-hidden bg-surface">
        <table className="w-full text-left text-sm">
          <thead className="bg-subtle border-b border-border">
            <tr>
              {columns.map((c, i) => <th key={i} className="px-4 py-3 font-medium text-secondary" style={{width: c.width}}>{c.header}</th>)}
            </tr>
          </thead>
          <tbody>
            {[...Array(5)].map((_, i) => (
              <tr key={i} className="border-b border-border last:border-0 h-[44px]">
                {columns.map((_, j) => (
                  <td key={j} className="px-4 py-2"><Skeleton className="h-4 w-full max-w-[100px]" /></td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  if (!data || data.length === 0) {
    return (
      <div className="border border-border rounded-lg bg-surface">
        <EmptyState title="No data" description={emptyMessage || "There are no records to display."} />
      </div>
    );
  }

  return (
    <div className="w-full border border-border rounded-lg overflow-hidden bg-surface">
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="bg-subtle border-b border-border sticky top-0">
            <tr>
              {columns.map((c, i) => (
                <th key={i} className="px-4 py-3 font-medium text-secondary" style={{width: c.width}}>
                  {c.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.map((row, rowIndex) => (
              <tr 
                key={rowIndex} 
                className={`border-b border-border last:border-0 h-[44px] hover:bg-subtle transition-colors ${onRowClick ? 'cursor-pointer' : ''}`}
                onClick={() => onRowClick?.(row)}
              >
                {columns.map((col, colIndex) => (
                  <td key={colIndex} className="px-4 py-2 whitespace-nowrap">
                    {col.render ? col.render(row) : row[col.key]}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
