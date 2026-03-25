// 父组件示例
import { Box, Title, Divider, Center, Stack } from "@mantine/core";
import BotCard from "./UserBot";
import { useMediaQuery } from '@mantine/hooks';

function UserBotList({ bots }) {
  // 将机器人分为两组
  const activeBots = bots.filter((bot) => !bot.deleted);
  const deletedBots = bots.filter((bot) => bot.deleted);
  const isMobile = useMediaQuery('(max-width: 768px)');

  return (
    <Box p="md">
      {/* 活跃机器人 */}
      <Box mb="xl">
        <Title order={4} mb="md">
          活跃机器人
        </Title>
        {isMobile ? (
          <Stack align="center" spacing="md">
            {activeBots.map((bot) => (
              <Box key={bot.id} style={{ width: '100%', maxWidth: 288 }}>
                <BotCard bot={bot} />
              </Box>
            ))}
          </Stack>
        ) : (
          <Center>
            <Box style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(288px, 1fr))', gap: 'md', width: '100%' }}>
              {activeBots.map((bot) => (
                <BotCard key={bot.id} bot={bot} />
              ))}
            </Box>
          </Center>
        )}
      </Box>

      {/* 分隔线 */}
      <Divider my="xl" />

      {/* 已删除机器人 */}
      {deletedBots.length > 0 && (
        <Box style={{ opacity: 0.7 }}>
          <Title order={4} mb="md" color="dimmed">
            已删除的机器人
          </Title>
          {isMobile ? (
            <Stack align="center" spacing="md">
              {deletedBots.map((bot) => (
                <Box key={bot.id} style={{ width: '100%', maxWidth: 288 }}>
                  <BotCard bot={bot} />
                </Box>
              ))}
            </Stack>
          ) : (
            <Center>
              <Box style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(288px, 1fr))', gap: 'md', width: '100%' }}>
                {deletedBots.map((bot) => (
                  <BotCard key={bot.id} bot={bot} />
                ))}
              </Box>
            </Center>
          )}
        </Box>
      )}
    </Box>
  );
}

export default UserBotList;
