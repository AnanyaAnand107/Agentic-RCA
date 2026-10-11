import { Copy, FileCode2 } from "lucide-react";
import { useState } from "react";

export function CodePanel({
  title,
  code,
  language = "text",
  maxHeight = "max-h-[430px]",
}: {
  title: string;
  code?: string | null;
  language?: string;
  maxHeight?: string;
}) {
  const [copied, setCopied] = useState(false);

  const value = code || "No output available.";

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);

      setCopied(true);

      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="overflow-hidden rounded-2xl border border-white/[.08] bg-[#0f1014]">
      <div className="flex items-center justify-between border-b border-white/[.07] px-5 py-4">
        <div className="flex min-w-0 items-center gap-2 text-base font-semibold text-zinc-100">
          <FileCode2 size={17} className="shrink-0 text-zinc-300" />
          {title}
          <span className="shrink-0 text-sm font-normal text-zinc-400">{language}</span>
        </div>
        <button
          onClick={copy}
          className="rounded-lg p-2 text-zinc-300 transition hover:bg-white/[.08] hover:text-white"
          title="Copy"
          aria-label={`Copy ${title}`}
        >
          <Copy size={15} />
        </button>
      </div>
      <pre
        className={`${maxHeight} overflow-auto bg-[#090a0d] p-5 font-mono text-sm leading-7 text-zinc-200`}
      >
        {value}
      </pre>
      {copied && (
        <div className="border-t border-white/[.08] px-5 py-3 text-sm text-emerald-200">
          Copied to clipboard.
        </div>
      )}
    </div>
  );
}
