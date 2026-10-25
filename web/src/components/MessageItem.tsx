import type { Message } from "@langchain/langgraph-sdk";
import { messageText } from "@/lib/messages";
import { Markdown } from "./Markdown";

export function MessageItem({ message }: { message: Message }) {
  if (message.type === "tool") {
    // Tool payloads may contain query summaries: show the tool name only.
    return (
      <li className="text-xs text-gray-500">
        <details>
          <summary className="cursor-pointer">
            Tool call: {message.name ?? "tool"}
          </summary>
          <p className="mt-1 italic">Tool output is hidden.</p>
        </details>
      </li>
    );
  }

  const text = messageText(message);
  if (message.type === "human") {
    return (
      <li className="self-end max-w-[85%] rounded-lg bg-blue-600 px-3 py-2 text-white">
        <span className="sr-only">You: </span>
        <p className="whitespace-pre-wrap break-words">{text}</p>
      </li>
    );
  }
  if (message.type === "ai") {
    if (!text) return null;
    return (
      <li className="max-w-[95%] rounded-lg bg-gray-50 px-3 py-2">
        <span className="sr-only">Assistant: </span>
        <Markdown>{text}</Markdown>
      </li>
    );
  }
  return null;
}
