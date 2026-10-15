import { type ClassValue, clsx } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatDate(date: string | Date): string {
  return new Date(date).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  })
}

export function getStatusColor(status: string): string {
  const colors: Record<string, string> = {
    draft: 'status-draft',
    staged: 'status-building',
    testing: 'status-testing',
    test_passed: 'status-published',
    test_failed: 'status-failed',
    published: 'status-published',
    failed: 'status-failed',
  }
  return colors[status] || 'status-draft'
}

export function formatXml(xml: string): string {
  try {
    const PADDING = '  '
    let formatted = ''
    let pad = 0
    
    xml.split(/>\s*</).forEach((node) => {
      if (node.match(/^\/\w/)) pad -= 1
      formatted += PADDING.repeat(pad) + '<' + node + '>\n'
      if (node.match(/^<?\w[^>]*[^\/]$/)) pad += 1
    })
    
    return formatted.substring(1, formatted.length - 2)
  } catch {
    return xml
  }
}





















