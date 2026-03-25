import TradingView from "Components/TradingView/TradingView"
import React, { useState, useEffect, useRef } from "react";
import { useRouter } from "next/router";
import {
  Button,
  TextInput,
  Modal,
  Loader,
  Box,
  Group,
  Text,
  Grid,
  Paper,
  Stack
} from "@mantine/core";
import { notifications } from '@mantine/notifications';
import { createOrder, getSymbol } from "services/trade.service";
import Draggable from 'react-draggable';

const Tradingview = () => {
  const {
    query: { symbol },
  } = useRouter();
  const containerRef = useRef(null);
  const [amount, setAmount] = useState(50);
  const [open, setOpen] = useState(false);
  const [marketData, setMarketData] = useState({ marketCap: 0, totalSupply: 0, percentChange_1h: 0, percentChange_1d: 0, percentChange_7d: 0}); // 假设的市场数据
  const [isLoading, setIsLoading] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    const handleResize = () => {
      if (containerRef.current) {
        const headerHeight =
          document.querySelector("header")?.offsetHeight || 80; // 假设头部高度为80px
        containerRef.current.style.height = `calc(100vh - ${headerHeight}px)`;
      }
    };

    handleResize();
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
    };
  }, []);

  const handleSubmit = async () => {
    try {
      setIsSubmitting(true);
      const result = await createOrder({
        symbol: symbol.substring(0, symbol.indexOf(".")),
        usdt: Number(amount),
      });
      notifications.show({
        title: '成功',
        message: '订单创建成功!',
        color: 'green',
      });
    } catch (error) {
      console.log(error.response);
      notifications.show({
        title: '错误',
        message: error?.response?.data?.error || "订单创建失败",
        color: 'red',
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleInfo = async () => {
    if (marketData.marketCap == 0) {
      try {
        setIsLoading(true);
        const s = symbol.replace("USDT.P", "").replace("1000", "").trim();
        const data = await getSymbol(s);
        const formatter = new Intl.NumberFormat('en-US', {
          style: 'decimal', // 格式化为十进制数字
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        });

        setMarketData({
          marketCap: formatter.format(data['quote']['USD']['market_cap']), // 示例数据
          totalSupply: formatter.format(data['quote']['USD']['fully_diluted_market_cap']), // 示例数据
          percentChange_1h: formatter.format(data['quote']['USD']['percent_change_1h']),
          percentChange_1d: formatter.format(data['quote']['USD']['percent_change_24h']),
          percentChange_7d: formatter.format(data['quote']['USD']['percent_change_7d']),
          volume_24h: formatter.format(data['quote']['USD']['volume_24h']),
          volume_change_24h: formatter.format(data['quote']['USD']['volume_change_24h']),
        });
      } catch (error) {
        notifications.show({
          title: '错误',
          message: '获取市场数据失败',
          color: 'red',
        });
      } finally {
        setIsLoading(false);
      }
    }
    setOpen(true);
  };

  const handleClose = () => {
    setOpen(false);
  };

  return (
    <div ref={containerRef}>
      <TradingView market={"BINANCE"} code={symbol} />
      <Draggable>
        <Paper className="fixed w-60 bottom-5 right-5 shadow-lg p-4 z-50" withBorder>
          <Stack gap="sm">
            <Button
              variant="outline"
              fullWidth
              onClick={handleInfo}
              disabled={isLoading}
              leftSection={isLoading ? <Loader size={16} /> : null}
            >
              {isLoading ? "加载中..." : "信息"}
            </Button>

            <TextInput
              label="数量"
              placeholder="输入数量"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              rightSection={<Text size="sm" c="dimmed">USDT</Text>}
              size="sm"
            />
            <Button
              variant="outline"
              fullWidth
              onClick={handleSubmit}
              disabled={isSubmitting}
              leftSection={isSubmitting ? <Loader size={16} /> : null}
            >
              {isSubmitting ? "提交中..." : "下单"}
            </Button>
          </Stack>
        </Paper>
      </Draggable>

      <Modal opened={open} onClose={handleClose} title={`${symbol} 信息`} size="lg">
        <Paper p="md" withBorder>
          <Grid gutter="sm" >
            <Grid.Col span={6}>
              <Group justify="space-between">
                <Text fw={600}>流通市值:</Text>
                <Text>${marketData.marketCap}</Text>
              </Group>
            </Grid.Col>
            <Grid.Col span={6}>
              <Group justify="space-between">
                <Text fw={600}>总市值:</Text>
                <Text>${marketData.totalSupply}</Text>
              </Group>
            </Grid.Col>
            <Grid.Col span={6}>
              <Group justify="space-between">
                <Text fw={600}>1h价格变化:</Text>
                <Text>{marketData.percentChange_1h}%</Text>
              </Group>
            </Grid.Col>
            <Grid.Col span={6}>
              <Group justify="space-between">
                <Text fw={600}>1d价格变化:</Text>
                <Text>{marketData.percentChange_1d}%</Text>
              </Group>
            </Grid.Col>
            <Grid.Col span={6}>
              <Group justify="space-between">
                <Text fw={600}>7d价格变化:</Text>
                <Text>{marketData.percentChange_7d}%</Text>
              </Group>
            </Grid.Col>
            <Grid.Col span={6}>
              <Group justify="space-between">
                <Text fw={600}>24h成交量:</Text>
                <Text>${marketData.volume_24h}</Text>
              </Group>
            </Grid.Col>
            <Grid.Col span={12}>
              <Group justify="space-between">
                <Text fw={600}>24h成交量变化量:</Text>
                <Text>${marketData.volume_change_24h}</Text>
              </Group>
            </Grid.Col>
          </Grid>
        </Paper>
        <Group justify="flex-end" mt="md">
          <Button onClick={handleClose} variant="filled">
            关闭
          </Button>
        </Group>
      </Modal>
    </div>
  );
};

export default Tradingview;
