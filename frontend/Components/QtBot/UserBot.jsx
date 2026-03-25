// BotCard.js
import React, { useState } from "react";
import {
  Button,
  Modal,
  Box,
  Text,
  Loader,
  Divider,
  ActionIcon,
  Stack,
  Group
} from "@mantine/core";
import { getBotTradeRecord } from "services/qtbot.service";
import BotCardSimple from "./BotCardSimple";
import { IconPlayerPlay, IconPlayerPause } from "@tabler/icons-react";
import { updateBotConfig } from "services/qtbot.service";

const BotCard = ({ bot }) => {
    const [tradeRecords, setTradeRecords] = useState([]);
    const [isModalOpen, setIsModalOpen] = useState(false);
    const [isLoading, setIsLoading] = useState(false);
    const [page, setPage] = useState(1);
    const [hasMore, setHasMore] = useState(true);
    const [botStatus, setBotStatus] = useState(bot.paused);
    const [botInfo, setBotInfo] = useState(bot);

    const updateBotStatus = async () => {
      try {
        const botInfos = await updateBotConfig(bot.id, { ...bot, paused: !botStatus });
        setBotInfo(botInfos);
        setBotStatus(!botStatus);
      } catch (error) {
        console.error("Error updating bot status:", error);
      }
    };

    const fetchTradeRecords = async (page = 1) => {
        page == 1 && setIsLoading(true);
        try {
            const response = await getBotTradeRecord(
              bot.id,
              "",
              page,
              "-timestamp"
            );            
            if (response.results.length > 0) {
                setTradeRecords(prevRecords => [...prevRecords, ...response.results]);
                setPage(page + 1);
            } 
        } catch (error) {
            console.error("Error fetching trade records:", error);
            if (error.response?.status == 404) {
              setHasMore(false);
            }
        } finally {
            setIsLoading(false);
        }
    };

    const handleScroll = (e) => {
        const bottom = e.target.scrollHeight - e.target.scrollTop === e.target.clientHeight;
        if (bottom && hasMore && !isLoading) {
            fetchTradeRecords(page);
        }
    };

    const getActionText = (record) => {
      return record.action == "buy"
        ? record.side == "LONG" ? "开多" : "平空"
        : record.side == "SHORT" ? "开空" : "平多";
    };

    const getActionColor = (record) => {
      return (record.action === "buy" && record.side == "LONG") ||
             (record.action === "sell" && record.side == "SHORT")
        ? "green" : "red";
    };

    const getProfitColor = (profit) => {
      return profit > 0 ? "green" : profit < 0 ? "red" : "dimmed";
    };

    return (
      <Box style={{ position: "relative", width: "100%", maxWidth: 288 }}>
        {isLoading && !isModalOpen && (
          <Box
            style={{
              position: "absolute",
              inset: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              backgroundColor: "rgba(255, 255, 255, 0.7)",
              zIndex: 1,
              borderRadius: 4,
            }}
          >
            <Loader />
          </Box>
        )}

        <BotCardSimple bot={botInfo} />
        
        <Box mt="sm" pb="sm">
          {!botInfo.deleted && <Button
            fullWidth
            variant={botStatus ? "filled" : "outline"}
            color={botStatus ? "green" : "red"}
            leftIcon={botStatus ? <IconPlayerPlay size={16} /> : <IconPlayerPause size={16} />}
            onClick={updateBotStatus}
            loading={isLoading}
            mb="xs"
          >
            {botStatus ? "启动机器人" : "暂停机器人"}
          </Button>}
          <Button
            fullWidth
            onClick={() => {
              fetchTradeRecords(1);
              setIsModalOpen(true);
            }}
            loading={isLoading && !isModalOpen}
          >
            交易记录
          </Button>
        </Box>

        <Modal
          opened={isModalOpen}
          onClose={() => {
            setTradeRecords([]);
            setIsModalOpen(false);
          }}
          title="交易记录"
          size="90%"
          style={{ maxHeight: "54vh" }}
        >
          <Box 
            style={{ overflowY: "auto", maxHeight: "40vh" }}
            onScroll={handleScroll}
          >
            {isLoading ? (
              <Group position="center" p="xl">
                <Loader />
              </Group>
            ) : tradeRecords.length > 0 ? (
              <Stack spacing="sm">
                {tradeRecords.map((record, index) => (
                  <div key={index}>
                    <Group position="apart" align="center" p="sm">
                      <Text size="sm" color="dimmed">
                        {record.timestamp}
                      </Text>
                      <Text
                        weight={500}
                        color={getActionColor(record)}
                      >
                        {getActionText(record)}
                      </Text>
                      <Text size="sm">
                        {record.amount} {record.symbol}
                      </Text>
                      <Text size="sm">价格: ${record.price}</Text>
                      <Text size="sm" color={getProfitColor(record.profit)}>
                        盈利: ${record.profit || 0.0}
                      </Text>
                    </Group>
                    {index < tradeRecords.length - 1 && <Divider />}
                  </div>
                ))}
              </Stack>
            ) : (
              <Box style={{ textAlign: "center", padding: "2rem" }}>
                <Text color="dimmed">暂无交易记录</Text>
              </Box>
            )}
          </Box>
        </Modal>
      </Box>
    );
};

export default BotCard;
