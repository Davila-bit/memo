import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

export type ButtonVariant = "primary" | "sage" | "coral" | "quiet" | "ghost";
export type ButtonSize = "md" | "lg" | "sm";

const base =
  "inline-flex items-center justify-center gap-2 rounded-full font-medium transition-[background-color,box-shadow,transform] duration-150 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-60 disabled:active:scale-100 select-none";

const variants: Record<ButtonVariant, string> = {
  primary: "bg-ink text-surface hover:bg-[#2c3a3f] shadow-[0_1px_0_rgb(0_0_0/0.08)]",
  sage: "bg-sage-ink text-surface hover:bg-[#335a4c]",
  coral: "bg-coral-ink text-surface hover:bg-[#922f25]",
  quiet: "bg-surface text-ink border border-line-strong/60 hover:bg-paper hover:border-line-strong",
  ghost: "text-ink hover:bg-ink/[0.06]",
};

// Every size keeps a 44 px minimum target (WCAG 2.5.5 enhanced; 2.5.8 needs 24 px).
const sizes: Record<ButtonSize, string> = {
  sm: "min-h-11 px-4 text-sm",
  md: "min-h-11 px-5 text-[0.9375rem]",
  lg: "min-h-14 px-7 text-[1.0625rem]",
};

export function buttonClasses(variant: ButtonVariant = "primary", size: ButtonSize = "md", className?: string): string {
  return cn(base, variants[variant], sizes[size], className);
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "primary", size = "md", className, type = "button", ...rest },
  ref,
) {
  return <button ref={ref} type={type} className={buttonClasses(variant, size, className)} {...rest} />;
});
