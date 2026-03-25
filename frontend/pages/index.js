import { Box, Text, SimpleGrid, Paper, ActionIcon, Modal, Loader, Grid, Title } from "@mantine/core";
import { useMediaQuery } from '@mantine/hooks';
import { useState, useEffect } from "react";
import Link from "next/link";
import SimpleEditor from "Components/Assistant/SimpleEditor";
import { getAllPlan, getCommonData } from "services/assistant.service";
import { getUserBotConfigs } from "services/qtbot.service";
import { getUserNotifyHistory } from "services/notify.service";
import { IconZoomIn, IconX } from '@tabler/icons-react';

export default function Home() {
  const [totalPlan, setTotalPlan] = useState(null);
  const [commonData, setCommonData] = useState(null);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [planContent, setPlanContent] = useState(null);
  const [notify, setNotify] = useState([]);

  const [botPerformance, setBotPerformance] = useState({
    dailyProfit: "+$ 0.0",
    totalProfitRate: "+0%",
    activeStrategies: "0/0"
  });
 
  const isDesktop = useMediaQuery('(min-width: 768px)');

  useEffect(() => {
    const fetchData = async () => {
      try {
        setLoading(true);
        // 获取当日交易计划        
        const planRes = await getAllPlan();
        if (planRes.data.results.length > 0) {
          setTotalPlan(planRes.data.results);
        }
        
        const commonData = await getCommonData();
        setCommonData(commonData.data);

        // 获取活跃机器人
        const botConfigs = await getUserBotConfigs();
        const active = botConfigs.filter(bot => !bot.deleted)

        const profit = active.reduce((sum, bot) => sum + bot.profit, 0)
        const cost = active.reduce((sum, bot) => sum + bot.usdt, 0)
        if (active.length > 0) {
          setBotPerformance({
            dailyProfit: "+$ " + parseFloat(profit).toFixed(2),
            totalProfitRate: "+" + parseFloat(profit / cost * 100).toFixed(2) + "%",
            activeStrategies: active.length + "/" + botConfigs.length
          })
        }

        const notify = await getUserNotifyHistory();
        setNotify(notify);
       
      } catch (error) {
        console.error("Error fetching data:", error);
      } finally {
        setLoading(false);
      }
    };
    
    fetchData();
  }, []);

  return (
    <Box p="md" style={{ backgroundColor: "#f3f0ff", minHeight: "100vh" }}>
      <Grid>
        {/* 市场数据面板 */}
        <Grid.Col span={{ base: 12, md: 6 }}>
          <Paper p="md" withBorder style={{ backgroundColor: "white", height: '100%' }}>
            <Title order={4} mb="md">当日行情概览</Title>
            <SimpleGrid cols={{ base: 2, md: 4 }} spacing="sm">
              <Box>
                <Text size="sm" color="dimmed">BTC</Text>
                <Title order={4} color="grape">${commonData?.BTC_price}</Title>
                <Text
                  size="sm"
                  color={commonData?.BTC_24h_change > 0 ? "green" : "red"}
                >
                  {commonData?.BTC_24h_change > 0 ? "+" : ""}{commonData?.BTC_24h_change}%
                </Text>
              </Box>
              <Box>
                <Text size="sm" color="dimmed">ETH</Text>
                <Title order={4} color="grape">${commonData?.ETH_price}</Title>
                <Text
                  size="sm"
                  color={commonData?.ETH_24h_change > 0 ? "green" : "red"}
                >
                  {commonData?.ETH_24h_change > 0 ? "+" : ""}{commonData?.ETH_24h_change}%
                </Text>
              </Box>
              <Box>
                <Text size="sm" color="dimmed">BTC ETF变化</Text>
                <Text
                  size="lg" weight={600}
                  color={commonData?.BTC_ETF_24h_inflow?.startsWith("-") ? "red" : "green"}
                >
                  {commonData?.BTC_ETF_24h_inflow?.startsWith("-") ? "" : "+"}{commonData?.BTC_ETF_24h_inflow} USDT
                </Text>
              </Box>
              <Box>
                <Text size="sm" color="dimmed">ETH ETF变化</Text>
                <Text
                  size="lg" weight={600}
                  color={commonData?.ETH_ETF_24h_inflow?.startsWith("-") ? "red" : "green"}
                >
                  {commonData?.ETH_ETF_24h_inflow?.startsWith("-") ? "" : "+"}{commonData?.ETH_ETF_24h_inflow} USDT
                </Text>
              </Box>
            </SimpleGrid>
          </Paper>
        </Grid.Col>

        {/* 机器人收益面板 */}
        <Grid.Col span={{ base: 12, md: 6 }}>
          <Paper p="md" withBorder style={{ backgroundColor: "white", height: '100%' }}>
            <Title order={4} mb="md">机器人收益</Title>
            <SimpleGrid cols={3} spacing="sm">
              <Box style={{ textAlign: "center" }}>
                <Title order={4} color="green">{botPerformance.dailyProfit || "-"}</Title>
                <Text size="sm">收益</Text>
              </Box>
              <Box style={{ textAlign: "center" }}>
                <Title order={4} color="blue">{botPerformance.totalProfitRate || "-"}</Title>
                <Text size="sm">总收益率</Text>
              </Box>
              <Box style={{ textAlign: "center" }}>
                <Title order={4} color="grape">{botPerformance.activeStrategies || "-"}</Title>
                <Text size="sm">运行策略</Text>
              </Box>
            </SimpleGrid>
          </Paper>
        </Grid.Col>

        {/* 今日交易计划 */}
        <Grid.Col span={{ base: 12, md: 7 }}>
          <Paper p="md" withBorder style={{ backgroundColor: "white", height: '100%' }}>
            <Box style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
              <Title order={4}>交易计划</Title>
              <Link href="/assistant/plan" style={{ textDecoration: "none" }}>
                <Text size="sm" color="grape">查看详情</Text>
              </Link>
            </Box>

            <Box style={{ maxHeight: 700, overflowY: "auto" }}>
              {totalPlan?.slice(0, 3).map((plan, index) => (
                <Box key={index} mb="md" p="md" style={{ backgroundColor: "#f3f0ff", borderRadius: 8 }}>
                  <Box style={{ display: "flex", alignItems: "center", marginBottom: "0.5rem" }}>
                    <Text weight={500} style={{ flex: 1 }}>
                      {plan.symbol == "ALL" ? "当日计划" : plan.symbol + " 计划"}
                    </Text>
                    <ActionIcon size="sm" mr="xs" onClick={() => {
                      setPlanContent(plan.content.content);
                      setOpen(true);
                    }}>
                      <IconZoomIn size={16} />
                    </ActionIcon>
                    <Text size="xs" p="xs" style={{ backgroundColor: "#e5dbff", color: "#6741d9", borderRadius: 4 }}>
                      {plan.date}
                    </Text>
                  </Box>
                  <Box mt="sm" p="md" style={{ backgroundColor: "#f8f6ff", height: 144, overflow: "hidden" }}>
                    <SimpleEditor initialContent={plan?.content.content} />
                  </Box>
                </Box>
              ))}
            </Box>
          </Paper>
        </Grid.Col>

        {/* 通知区域 */}
        <Grid.Col span={{ base: 12, md: 5 }}>
          <Paper p="md" withBorder style={{ backgroundColor: "white", height: '100%' }}>
            <Title order={4} mb="md">通知中心</Title>
            {loading ? (
              <Box style={{ display: "flex", justifyContent: "center", alignItems: "center", height: 200 }}>
                <Loader />
              </Box>
            ) : (
              <Box style={{ maxHeight: 700, overflowY: "auto" }}>
                {notify.slice(0, 10).map((item, index) => {
                  const bgColors = ["#fffbf0", "#f0fdf4", "#f0f8ff"];
                  const borderColors = ["#fbbf24", "#10b981", "#3b82f6"];
                  const colorIndex = index % 3;

                  return (
                    <Box
                      key={index}
                      p="md"
                      mb="sm"
                      style={{
                        borderLeft: `4px solid ${borderColors[colorIndex]}`,
                        backgroundColor: bgColors[colorIndex],
                        borderRadius: "0 8px 8px 0"
                      }}
                    >
                      <Text weight={500} size="sm">
                        {item.message.split("-").length > 1 ? item.message.split("-")[1] : 'Unknown'}
                      </Text>
                      <Text color="dimmed" size="sm">
                        {item.message.split("-")[0]}
                      </Text>
                      <Text color="dimmed" size="xs">
                        {new Date(item.time * 1000).toLocaleString()}
                      </Text>
                    </Box>
                  );
                })}
              </Box>
            )}
          </Paper>
        </Grid.Col>

        {/* 快捷操作区域 */}
        {/* <Grid.Col span={12}>
          <Paper p="md" withBorder style={{ backgroundColor: "white" }}>
            <Title order={4} mb="md">快捷操作</Title>
            <SimpleGrid cols={{ base: 4, sm: 8 }} spacing="sm">
              {[
                { icon: "📊", title: "交易系统" },
                { icon: "📝", title: "交易计划" },
                { icon: "📈", title: "交易总结" },
                { icon: "📋", title: "编辑模板" },
                { icon: "👥", title: "社交中心" },
                { icon: "🤖", title: "量化策略" },
                { icon: "➕", title: "新建计划" },
                { icon: "👁️", title: "交易单" }
              ].map((item, index) => (
                <Box key={index} style={{ textAlign: 'center' }}>
                  <Paper p="lg" radius="md" withBorder style={{ backgroundColor: '#f3f0ff', cursor: 'pointer' }}>
                    <Text size="xl">{item.icon}</Text>
                  </Paper>
                  <Text size="xs" mt="xs">{item.title}</Text>
                </Box>
              ))}
            </SimpleGrid>
          </Paper>
        </Grid.Col> */}
      </Grid>
      
      <Modal
        opened={open}
        onClose={() => setOpen(false)}
        title="交易计划"
        size="90%"
      >
        <Box style={{ height: "400px", overflow: "auto" }}>
          <SimpleEditor initialContent={planContent} />
        </Box>
      </Modal>
    </Box>
  );
}
