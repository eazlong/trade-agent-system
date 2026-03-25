// "use client";
import React, { useState, useEffect } from "react";
import {
  Table,
  Box,
  Title,
  Loader,
  Button,
  TextInput,
  ActionIcon,
  Badge,
  Text,
  Group,
  Stack,
  Paper
} from "@mantine/core";
import { 
  IconTrendingUp, 
  IconTrendingDown, 
  IconLineDotted, 
  IconSearch, 
  IconX, 
} from '@tabler/icons-react';
import {getHotCoin, getPlan, createPlan} from "services/assistant.service";
import TradingPlanModal from "./TradingPlanModal";
import { useToasts } from "react-toast-notifications";
import Link from "next/link";
import dayjs from "dayjs";

const HotCoin = () => {
  const [coins, setCoins] = useState([]);
  const [loading, setLoading] = useState(true);
  const [userPlanContent, setUserPlanContent] = useState(null);
  const [userCheckCondition, setUserCheckCondition] = useState(null);
  const [searchTerm, setSearchTerm] = useState("");
  const [filteredCoins, setFilteredCoins] = useState([]);
  const [commonPairs, setCommonPairs] = useState(["BTC", "ETH", "SOL", "LINK", "DOT"]);

  const [openModal, setOpenModal] = useState(false);
  const [selectedSymbol, setSelectedSymbol] = useState("");

  const { addToast } = useToasts();
  const showToast = (message, appearance) => {
    addToast(message, { appearance });
  };

  const handleOpenModal = async (symbol) => {
    setSelectedSymbol(symbol);
    try {
      const response = await getPlan(symbol, null);
      if (response.status === 200) {
        if (response.data.content) {
          setUserPlanContent(response.data.content);
          setUserCheckCondition(response.data.condition);
        } else {
          const response = await createPlan(symbol,  { date: dayjs().format("YYYY-MM-DD"), type: "today" })
          if (response.status === 200) {
            console.log(response.data);
            setUserPlanContent(response.data.content);
            setUserCheckCondition(response.data.condition);
          } else {
            showToast("获取交易计划失败");
          }
        }
      } else {
        showToast("获取交易计划失败");
      }
    } catch (error) {
      console.error("Error fetching or creating plan:", error);
    }
    setOpenModal(true);
  };

  const handleCloseModal = () => {
    setOpenModal(false);
  };

  const handleSearchChange = (event) => {
    setSearchTerm(event.target.value);
  };

  const handleClearSearch = () => {
    setSearchTerm("");
  };

  const handleChipClick = (pair) => {
    setSearchTerm(pair);
  };

  useEffect(() => {
    const fetchHotCoins = async () => {
      try {
        const response = await getHotCoin();
        console.log(response);
        
        setCoins(response.data);
        setLoading(false);
      } catch (error) {
        console.error("Error fetching hot coins:", error);
        setLoading(false);
      }
    };

    fetchHotCoins();
  }, []);

  useEffect(() => {
    if (!searchTerm.trim()) {
      setFilteredCoins(coins);
      return;
    }
    
    const filtered = coins.filter((coin) =>
      coin.symbol.toLowerCase().includes(searchTerm.toLowerCase())
    );
    setFilteredCoins(filtered);
  }, [searchTerm, coins]);

  const getTrendIcon = (trend) => {
    const color = trend > 0 ? 'green' : trend < 0 ? 'red' : 'gray';
    return trend > 0 ? <IconTrendingUp size={16} style={{ color }} /> : trend < 0 ? <IconTrendingDown size={16} style={{ color }} /> : <IconLineDotted size={16} style={{ color }} />;
  };

  const formatBreakTime = (breakTime) => {
    if (breakTime === 0) return "-";
    const currentTime = Date.now() / 1000;
    const timeDifference = currentTime - breakTime;
    const days = Math.floor(timeDifference / (60 * 60 * 24));
    const hours = Math.floor((timeDifference % (60 * 60 * 24)) / (60 * 60));
    return `${days} 天 ${hours} 小时前`;
  };

  return (
    <Box p="md">
      <Group position="apart" align="flex-start" mb="md">
        <Title order={2} mb="xs">
          推荐币种
        </Title>
        <Stack spacing="sm" style={{ width: "100%", maxWidth: 300 }}>
          <TextInput
            placeholder="搜索币种..."
            value={searchTerm}
            onChange={handleSearchChange}
            leftSection={<IconSearch size={16} />}
            rightSection={
              searchTerm && (
                <ActionIcon size="sm" onClick={handleClearSearch}>
                  <IconX size={14} />
                </ActionIcon>
              )
            }
          />
          <Group spacing="xs">
            {commonPairs.map((pair) => (
              <Badge
                key={pair}
                variant={searchTerm === pair ? "filled" : "outline"}
                color={searchTerm === pair ? "blue" : "gray"}
                style={{ cursor: "pointer" }}
                onClick={() => handleChipClick(pair)}
              >
                {pair}
              </Badge>
            ))}
          </Group>
        </Stack>
      </Group>

      {loading ? (
        <Group position="center" style={{ height: 160 }}>
          <Loader />
        </Group>
      ) : (
        <Stack spacing="md">
          <TradingPlanModal
            symbol={selectedSymbol}
            open={openModal}
            onClose={handleCloseModal}
            userPlanContent={userPlanContent}
            userCheckCondition={userCheckCondition}
          />
          
          {filteredCoins.length === 0 ? (
            <Paper p="xl" style={{ textAlign: "center", backgroundColor: "#f8f9fa" }}>
              <Text color="dimmed">没有找到匹配的币种</Text>
            </Paper>
          ) : (
            <Paper withBorder>
              <Table>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>交易对</Table.Th>
                    <Table.Th>15m趋势</Table.Th>
                    <Table.Th>1h趋势</Table.Th>
                    <Table.Th>4h趋势</Table.Th>
                    <Table.Th>1d趋势</Table.Th>
                    <Table.Th>突破时间</Table.Th>
                    <Table.Th>波动次数</Table.Th>
                    <Table.Th>流通量</Table.Th>
                    <Table.Th>总供应量</Table.Th>
                    <Table.Th>操作</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {filteredCoins.map((coin) => (
                    <Table.Tr key={coin.symbol}>
                      <Table.Td>
                        <Link
                          href={`/tradingview/${coin.symbol}.P`}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          <Text
                            component="a"
                            weight={500}
                            color="grape"
                            style={{ textDecoration: "none" }}
                            sx={{ "&:hover": { textDecoration: "underline" } }}
                          >
                            {coin.symbol}
                          </Text>
                        </Link>
                      </Table.Td>
                      <Table.Td>{getTrendIcon(coin.trend_15m)}</Table.Td>
                      <Table.Td>{getTrendIcon(coin.trend_1h)}</Table.Td>
                      <Table.Td>{getTrendIcon(coin.trend_4h)}</Table.Td>
                      <Table.Td>{getTrendIcon(coin.trend_1d)}</Table.Td>
                      <Table.Td>
                        <Text size="sm">{formatBreakTime(coin.break_time)}</Text>
                      </Table.Td>
                      <Table.Td>
                        <Text size="sm">{coin.big_wave_count}</Text>
                      </Table.Td>
                      <Table.Td>
                        <Text size="sm">{(coin.circulating_supply / 1e6).toFixed(2)}M</Text>
                      </Table.Td>
                      <Table.Td>
                        <Text size="sm">{(coin.total_supply / 1e6).toFixed(2)}M</Text>
                      </Table.Td>
                      <Table.Td>
                        <Button
                          size="xs"
                          variant="subtle"
                          color="grape"
                          onClick={() => handleOpenModal(coin.symbol)}
                        >
                          交易计划
                        </Button>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Paper>
          )}
        </Stack>
      )}
    </Box>
  );
};

export default HotCoin;
