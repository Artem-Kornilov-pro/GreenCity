import type { HTMLAttributes } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "../../lib/utils";

const badgeVariants = cva("inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium", {
  variants: {
    variant: {
      brand: "bg-brand-100 text-brand-700",
      neutral: "bg-ink-100 text-ink-600",
      danger: "bg-danger-500/10 text-danger-500",
      warning: "bg-warning-500/15 text-ink-700",
      success: "bg-success-500/10 text-success-500",
    },
  },
  defaultVariants: { variant: "neutral" },
});

export function Badge({ className, variant, ...props }: HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
