"use client";

import { useState, useRef, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchChatHistory, sendChatMessage, type ChatMessage } from "@/lib/api";

export default function ChatPage() {
  const [input, setInput] = useState("");
  const [waiting, setWaiting] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();

  // Fetch chat history - poll every 2s when waiting for response
  const { data: messages = [] } = useQuery({
    queryKey: ["chat-history"],
    queryFn: () => fetchChatHistory(),
    refetchInterval: waiting ? 2000 : 10000,
  });

  // Send message mutation
  const sendMutation = useMutation({
    mutationFn: sendChatMessage,
    onSuccess: () => {
      setWaiting(true);
      queryClient.invalidateQueries({ queryKey: ["chat-history"] });
    },
  });

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    // Stop waiting when we get a new agent message
    if (messages.length > 0) {
      const last = messages[messages.length - 1];
      if (last.role === "agent" && waiting) {
        setWaiting(false);
      }
    }
  }, [messages, waiting]);

  const handleSend = () => {
    if (!input.trim()) return;
    sendMutation.mutate(input.trim());
    setInput("");
  };

  const agentColor = (name: string) => {
    const colors: Record<string, string> = {
      risk_officer: "bg-red-100 text-red-800 border-red-300",
      strategist: "bg-blue-100 text-blue-800 border-blue-300",
      manager: "bg-orange-100 text-orange-800 border-orange-300",
      developer: "bg-green-100 text-green-800 border-green-300",
      general: "bg-gray-100 text-gray-800 border-gray-300",
    };
    return colors[name] || colors.general;
  };

  const agentLabel = (name: string) => {
    const labels: Record<string, string> = {
      risk_officer: "Risk Officer",
      strategist: "Strategist",
      manager: "Manager",
      developer: "Developer",
      general: "Assistant",
    };
    return labels[name] || name;
  };

  return (
    <div className="flex flex-col h-[calc(100vh-4rem)]">
      <div className="border-b px-6 py-4">
        <h1 className="text-xl font-semibold">Chat avec les agents</h1>
        <p className="text-sm text-gray-500">
          Parlez en langage naturel avec le Risk Officer, le Strategist, le Manager ou le Developer.
        </p>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
        {messages.length === 0 && (
          <div className="text-center text-gray-400 mt-20">
            <p className="text-lg">Aucun message</p>
            <p className="text-sm mt-2">Ecrivez un message pour commencer une conversation.</p>
          </div>
        )}
        {messages.map((msg: ChatMessage) => (
          <div
            key={msg.id}
            className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[70%] rounded-lg px-4 py-3 ${
                msg.role === "user"
                  ? "bg-indigo-600 text-white"
                  : "bg-white border border-gray-200"
              }`}
            >
              {msg.role === "agent" && (
                <span
                  className={`inline-block text-xs font-medium px-2 py-0.5 rounded mb-2 border ${agentColor(
                    msg.agent_name
                  )}`}
                >
                  {agentLabel(msg.agent_name)}
                </span>
              )}
              <p className="whitespace-pre-wrap text-sm">{msg.message}</p>
              <p
                className={`text-xs mt-1 ${
                  msg.role === "user" ? "text-indigo-200" : "text-gray-400"
                }`}
              >
                {new Date(msg.created_at).toLocaleTimeString("fr-FR")}
              </p>
            </div>
          </div>
        ))}
        {waiting && (
          <div className="flex justify-start">
            <div className="bg-white border border-gray-200 rounded-lg px-4 py-3">
              <div className="flex space-x-1">
                <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" />
                <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce [animation-delay:0.1s]" />
                <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce [animation-delay:0.2s]" />
              </div>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="border-t px-6 py-4">
        <div className="flex space-x-3">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && handleSend()}
            placeholder="Ecrivez un message... (ex: 'Quelle est notre exposition actuelle ?')"
            className="flex-1 rounded-lg border border-gray-300 px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            disabled={waiting}
          />
          <button
            onClick={handleSend}
            disabled={!input.trim() || waiting}
            className="bg-indigo-600 text-white px-6 py-2 rounded-lg text-sm font-medium hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Envoyer
          </button>
        </div>
      </div>
    </div>
  );
}
