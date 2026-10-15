"use client"

import * as React from "react"
import { format, parse, isValid } from "date-fns"
import { CalendarIcon } from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Calendar } from "@/components/ui/calendar"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"

interface DatePickerProps {
  value: string                    // Oracle date string e.g. "2024-01-15"
  onChange: (value: string) => void
  placeholder?: string
  className?: string
  dateFormat?: string              // date-fns format, default yyyy-MM-dd
  compact?: boolean                // Compact sizing for inline use
}

/**
 * Date picker that stores value as a string (for Oracle bind variables).
 * Provides both a text input (with format hint) and a calendar popover.
 */
export function DatePicker({
  value,
  onChange,
  placeholder,
  className,
  dateFormat = "yyyy-MM-dd",
  compact = false,
}: DatePickerProps) {
  const [open, setOpen] = React.useState(false)

  const displayPlaceholder = placeholder || dateFormat.toUpperCase()

  // Parse the string value into a Date for the calendar
  const dateValue = React.useMemo(() => {
    if (!value) return undefined
    const parsed = parse(value, dateFormat, new Date())
    return isValid(parsed) ? parsed : undefined
  }, [value, dateFormat])

  const handleCalendarSelect = (date: Date | undefined) => {
    if (date) {
      onChange(format(date, dateFormat))
    } else {
      onChange("")
    }
    setOpen(false)
  }

  const handleInputBlur = () => {
    if (!value) return
    const parsed = parse(value, dateFormat, new Date())
    if (isValid(parsed)) {
      onChange(format(parsed, dateFormat))
    }
  }

  const sizeClasses = compact ? "h-7 text-xs" : "h-10 text-sm"

  return (
    <div className={cn("flex items-center", className)}>
      <Input
        className={cn(
          sizeClasses,
          "rounded-r-none border-r-0 font-mono flex-1 min-w-0",
        )}
        placeholder={displayPlaceholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onBlur={handleInputBlur}
      />
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            type="button"
            className={cn(
              sizeClasses,
              "rounded-l-none border-l-0 px-2 shrink-0",
            )}
          >
            <CalendarIcon className={compact ? "h-3 w-3" : "h-4 w-4"} />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-auto p-0 z-[100]" align="end">
          <Calendar
            mode="single"
            selected={dateValue}
            onSelect={handleCalendarSelect}
            initialFocus
          />
        </PopoverContent>
      </Popover>
    </div>
  )
}
