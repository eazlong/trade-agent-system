import React from "react";
import { Box, Text, Card, Group, Stack, Title, Button, Badge } from "@mantine/core";
import { IconRobot } from "@tabler/icons-react";

const BotCardSimple = ({ bot }) => {

  const getPositionColor = (side) => {
    return side === "LONG" ? "green" : side === "SHORT" ? "red" : "dark";
  };

  const getProfitColor = (profit) => {
    return profit >= 0 ? "green" : "red";
  };

  return (
    <Card withBorder p="md" mb="md" style={{ backgroundColor: "#f3f0ff" }}>
      <Group align="flex-start" mb="md">
        <IconRobot size={40} style={{ color: "var(--mantine-color-grape-6)" }} />
        <Box style={{ flex: 1 }}>
          <Group align="center" spacing="sm">
            <Title order={4}>{bot.bot_name}</Title>
            {!bot.deleted && <Badge 
              color={bot.paused ? "red" : "green"} 
              variant="filled" 
              size="sm"
            >
              {bot.paused ? "已暂停" : "运行中"}
            </Badge>}
          </Group>
          <Text size="sm" color="dimmed">{bot.strategy}</Text>
        </Box>
      </Group>
      
      <Title order={4} mt="sm">{bot.symbol}</Title>
      
      <Group position="apart" mt="md">
        <Box>
          <Text color="dimmed" size="sm" mb="xs">当前仓位</Text>
          <Text
            weight={600}
            color={getPositionColor(bot?.position_side)}
          >
            {bot?.position_side === "LONG" ? "多头" : bot?.position_side === "SHORT" ? "空头" : "无"}
          </Text>
          {bot?.position_amount && (
            <Text size="sm" color="dimmed">
              {bot.position_amount}
            </Text>
          )}
        </Box>
        <Box style={{ textAlign: "right" }}>
          <Text color="dimmed" size="sm" mb="xs">未实现盈亏</Text>
          <Text
            weight={600}
            color={getProfitColor(bot?.unrealized_profit)}
          >
            {bot?.unrealized_profit ? bot.unrealized_profit : "0.00"} USDT
          </Text>
        </Box>
      </Group>
      
      <Box mt="md">
        <Text color="dimmed" size="sm" mb="xs">已实现利润</Text>
        <Title order={3} color="green">
          {bot.profit || "0.00"} USDT
        </Title>
      </Box>
    </Card>
  );
};

export default BotCardSimple; 