import { createContext, createElement, useContext, useMemo, useState, type ReactNode } from "react";
import en from "@/locales/en.json";
import zh from "@/locales/zh.json";

export type Language = "zh" | "en";
type Dictionary = typeof zh;
type Params = Record<string, string | number>;

const STORAGE_KEY = "vibe-language";
const dictionaries: Record<Language, Dictionary> = { zh, en };

interface I18nContextValue {
  lang: Language;
  setLanguage: (lang: Language) => void;
  t: (key: string, params?: Params) => string;
}

const I18nContext = createContext<I18nContextValue | null>(null);

function getInitialLanguage(): Language {
  if (typeof window === "undefined") return "zh";
  const stored = window.localStorage.getItem(STORAGE_KEY);
  return stored === "en" || stored === "zh" ? stored : "zh";
}

function resolveValue(dict: Dictionary, key: string): string | undefined {
  const value = key.split(".").reduce<unknown>((acc, part) => {
    if (acc && typeof acc === "object" && part in acc) {
      return (acc as Record<string, unknown>)[part];
    }
    return undefined;
  }, dict);
  return typeof value === "string" ? value : undefined;
}

function interpolate(template: string, params?: Params): string {
  if (!params) return template;
  return template.replace(/\{\{(\w+)\}\}/g, (_, key: string) => String(params[key] ?? ""));
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLang] = useState<Language>(() => getInitialLanguage());

  const value = useMemo<I18nContextValue>(() => {
    const setLanguage = (next: Language) => {
      setLang(next);
      window.localStorage.setItem(STORAGE_KEY, next);
      document.documentElement.lang = next === "zh" ? "zh-CN" : "en";
    };

    const t = (key: string, params?: Params) => {
      const current = resolveValue(dictionaries[lang], key);
      const fallback = resolveValue(dictionaries.en, key) || key;
      return interpolate(current || fallback, params);
    };

    return { lang, setLanguage, t };
  }, [lang]);

  return createElement(I18nContext.Provider, { value }, children);
}

export function useTranslation() {
  const context = useContext(I18nContext);
  if (!context) {
    throw new Error("useTranslation must be used within I18nProvider");
  }
  return context;
}
