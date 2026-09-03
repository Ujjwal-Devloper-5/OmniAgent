import React from 'react';

export default function Skeleton({ className = '' }) {
  return (
    <div className={`shimmer rounded ${className}`}></div>
  );
}
