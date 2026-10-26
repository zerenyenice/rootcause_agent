import { memo } from "react";
import type { Segment } from "@/lib/activity";
import { ActivityTimeline } from "./ActivityTimeline";
import { AssistantMessage, UserMessage } from "./MessageBubble";
import { WhyChainCard } from "./WhyChainCard";

interface ConversationProps {
  segments: Segment[];
  isRunning: boolean;
  durations: Readonly<Record<number, number>>;
}

function ConversationImpl({ segments, isRunning, durations }: ConversationProps) {
  const lastIndex = segments.length - 1;
  return (
    <ol aria-label="Conversation" className="flex flex-col gap-5">
      {segments.map((segment, index) => {
        switch (segment.kind) {
          case "human":
            return <UserMessage key={segment.key} message={segment.message} />;
          case "text":
            return <AssistantMessage key={segment.key} message={segment.message} />;
          case "whychain":
            return (
              <li key={segment.key}>
                <WhyChainCard chain={segment.chain} />
              </li>
            );
          case "activity":
            return (
              <li key={segment.key}>
                <ActivityTimeline
                  steps={segment.steps}
                  isLive={isRunning && index === lastIndex}
                  durationMs={durations[segment.turn]}
                />
              </li>
            );
        }
      })}
    </ol>
  );
}

export const Conversation = memo(ConversationImpl);
