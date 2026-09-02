import Link from "next/link"

import { useTheme } from "@/components/theme-provider"
import { cn } from "@/lib/utils"

interface LogoProps {
  variant?: "full" | "icon" | "responsive"
  className?: string
  asLink?: boolean
}

export function Logo({
  variant = "full",
  className,
  asLink = true,
}: LogoProps) {
  const { resolvedTheme } = useTheme()
  const isDark = resolvedTheme === "dark"

  const fullLogo = isDark ? "/assets/images/fastapi-logo-light.svg" : "/assets/images/fastapi-logo.svg"
  const iconLogo = isDark ? "/assets/images/fastapi-icon-light.svg" : "/assets/images/fastapi-icon.svg"

  const content =
    variant === "responsive" ? (
      <>
        <img
          src={fullLogo}
          alt="FastAPI"
          className={cn(
            "h-6 w-auto group-data-[collapsible=icon]:hidden",
            className,
          )}
        />
        <img
          src={iconLogo}
          alt="FastAPI"
          className={cn(
            "size-5 hidden group-data-[collapsible=icon]:block",
            className,
          )}
        />
      </>
    ) : (
      <img
        src={variant === "full" ? fullLogo : iconLogo}
        alt="FastAPI"
        className={cn(variant === "full" ? "h-6 w-auto" : "size-5", className)}
      />
    )

  if (!asLink) {
    return content
  }

  return <Link href="/">{content}</Link>
}
