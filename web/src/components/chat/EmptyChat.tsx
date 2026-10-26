import { Sparkles } from "lucide-react";

export const EXAMPLE_QUESTIONS = [
  "Why did scrap rate rise last month?",
  "Which plant had the biggest drop in daily output, and why?",
  "What caused the spike in downtime on line 3?",
  "Why did on-time delivery get worse this quarter?",
] as const;

interface EmptyChatProps {
  connectionName: string;
  onPick: (question: string) => void;
}

export function EmptyChat({ connectionName, onPick }: EmptyChatProps) {
  return (
    <div className="rc-fade-in flex flex-col items-center px-4 pt-[12vh] text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-accent/10 text-accent-text">
        <Sparkles aria-hidden="true" className="h-6 w-6" />
      </div>
      <h1 className="mt-5 text-2xl font-semibold tracking-tight">
        What do you want to understand?
      </h1>
      <p className="mt-2 text-muted">
        Ask why something happened. RootCause will investigate{" "}
        <span className="font-medium text-fg">{connectionName}</span> and trace it back to
        the root cause.
      </p>
      <ul
        aria-label="Example questions"
        className="mt-8 grid w-full max-w-xl gap-2 sm:grid-cols-2"
      >
        {EXAMPLE_QUESTIONS.map((question) => (
          <li key={question} className="flex">
            <button
              type="button"
              onClick={() => onPick(question)}
              className="w-full rounded-card border border-line bg-surface p-3 text-left transition-colors hover:border-accent/50 hover:bg-subtle"
            >
              {question}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
