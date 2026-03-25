import React, { useState, useCallback } from "react";
import useWebSocket, { ReadyState } from "react-use-websocket";
import { useSelector } from "react-redux";
import Link from "next/link";
import { HOST } from "services/index";
import {
  Loader,
  Divider,
  Text,
  Paper,
  Button,
  Badge,
  Box,
  Group,
  Stack,
} from "@mantine/core";
import { IconRefresh, IconSettings } from '@tabler/icons-react';
import { getUserNotifyHistory } from "services/notify.service";

const NotifityWebSocketComponent = () => {
  const [socketUrl, setSocketUrl] = useState(`wss://${HOST}/ws/notify/`);
  const [messageHistory, setMessageHistory] = useState([]);
  const { accessToken } = useSelector((state) => state.auth);
  const [reconnectInterval, setReconnectInterval] = useState(null);
  const [isLoading, setIsLoading] = useState(true);

  const { sendMessage, lastMessage, readyState } = useWebSocket(
    accessToken ? socketUrl : null,
    {
      protocols: accessToken ? ["authorization", `${accessToken}`] : [],
      retryOnError: true,
      onClose: () => {
        setIsLoading(true);
        if (!reconnectInterval) {
          setReconnectInterval(
            setInterval(() => {
              setSocketUrl((prevUrl) => {
                console.log(`${readyState} -- ${prevUrl}`);
                if (!prevUrl || readyState === ReadyState.CLOSED) {
                  return `wss://${HOST}/ws/notify/?t=${Date.now()}`;
                }
                return prevUrl;
              });
            }, 3000)
          );
        }
      },
      onOpen: () => {
        setIsLoading(false);
        getNotifyHistory();
        if (reconnectInterval) {
          clearInterval(reconnectInterval);
          setReconnectInterval(null);
          setSocketUrl(`wss://${HOST}/ws/notify/`);
        }
      },
      shouldReconnect: () => true,
    }
  );

  const getNotifyHistory = async () => {
    try {
      const data = await getUserNotifyHistory();
      setMessageHistory(data);
    } catch (error) {
      console.error("Failed to fetch notification history:", error);
    }
  };

  React.useEffect(() => {
    if (lastMessage !== null) {
      console.log(lastMessage.data);
      const parsedData =
        typeof lastMessage.data === "string"
          ? JSON.parse(lastMessage.data)
          : lastMessage.data;
      setMessageHistory((prev) => [parsedData].concat(prev));
    }
  }, [lastMessage]);

  const connectionStatus = {
    [ReadyState.CONNECTING]: "Connecting",
    [ReadyState.OPEN]: "Open",
    [ReadyState.CLOSING]: "Closing",
    [ReadyState.CLOSED]: "Closed",
    [ReadyState.UNINSTANTIATED]: "Uninstantiated",
  }[readyState];

  const getConnectionStatusColor = () => {
    switch (readyState) {
      case ReadyState.OPEN:
        return "success";
      case ReadyState.CONNECTING:
        return "warning";
      case ReadyState.CLOSED:
      case ReadyState.CLOSING:
      default:
        return "error";
    }
  };

  const renderMessageContent = (message) => {
    if (!message) {
      return null;
    }

    try {
      const msg = message["message"].split(/{|}/);
      const timestamp = new Date(message.time * 1000).toLocaleString();

      return (
        <Stack spacing={0}>
          <Text size="sm" c="dimmed">
            {timestamp}
          </Text>
          <Text>
            {msg[0]}
            <Link
              href={`tradingview/${msg[1]}.P`}
              target="_blank"
              style={{
                color: "#2196f3",
                margin: "0 4px",
                textDecoration: "none",
              }}
            >
              {msg[1]}
            </Link>
            {msg[2]}
          </Text>
        </Stack>
      );
    } catch (error) {
      console.error("Invalid JSON string:", error.message);
      return (
        <Text c="red">
          Error parsing message
        </Text>
      );
    }
  };

  return (
    <Box
      style={{
        padding: '24px',
        height: "calc(100vh - 80px)",
        display: "flex",
        flexDirection: "column",
      }}
      className="bg-purple-100"
    >
      <Group align="center" style={{ marginBottom: '16px' }}>
        <Text size="sm" weight={500}>WebSocket Status:</Text>
        <Badge
          color={getConnectionStatusColor()}
          variant="outline"
          size="sm"
          leftSection={<IconRefresh />}
        >
          {connectionStatus}
        </Badge>

        {readyState === ReadyState.CLOSED && (
          <Button
            leftSection={<IconRefresh />}
            variant="outline"
            size="sm"
            onClick={() => window.location.reload()}
          >
            重连
          </Button>
        )}

        <Link href="notify/config" target="_blank" style={{ marginLeft: 'auto' }}>
          <Button
            leftSection={<IconSettings />}
            size="sm"
            variant="subtle"
            className="text-purple-500"
          >
            配置
          </Button>
        </Link>
      </Group>

      {isLoading ? (
        <Box
          style={{
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            flexGrow: 1,
          }}
        >
          <Loader />
        </Box>
      ) : (
        <Paper
          style={{
            flexGrow: 1,
            overflow: "auto",
            border: "1px solid #e9ecef",
            borderRadius: '8px',
          }}
        >
          <Stack spacing={0} style={{ width: "100%" }}>
            {messageHistory.map((message, index) => (
              <React.Fragment key={index}>
                <Box
                  className={index % 2 ? "bg-purple-300" : "bg-purple-200"}
                  style={{ padding: '12px 16px', borderRadius: '8px' }}
                >
                  {renderMessageContent(message)}
                </Box>
                {index < messageHistory.length - 1 && <Divider height={1}/>}
              </React.Fragment>
            ))}
          </Stack>
        </Paper>
      )}
    </Box>
  );
};

export default NotifityWebSocketComponent;
