import type { Message } from "@langchain/langgraph-sdk";
import { messageText } from "@/lib/messages";
import { Markdown } from "../Markdown";

export function UserMessage({ message }: { message: Message }) {
  return (
    <li className="rc-fade-in flex justify-end">
      <div className="max-w-[85%] rounded-2xl bg-subtle px-4 py-2.5">
        <span className="sr-only">You: </span>
        <p className="break-words whitespace-pre-wrap">{messageText(message)}</p>
      </div>
    </li>
  );
}

export function AssistantMessage({ message }: { message: Message }) {
  return (
    <li className="rc-fade-in">
      <span className="sr-only">Assistant: </span>
      <Markdown>{messageText(message)}</Markdown>
    </li>
  );
}
