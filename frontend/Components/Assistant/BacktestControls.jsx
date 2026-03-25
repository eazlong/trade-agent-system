import React, { useState, useRef, useEffect } from 'react';
import { Button, Group, Stack, Text } from '@mantine/core';
import { DatePicker } from '@mantine/dates';
import { IconPlayerPlay, IconPlayerPause, IconPlayerSkipBack, IconPlayerSkipForward } from '@tabler/icons-react';

export default function BacktestControls({ 
  isBacktesting, 
  onStartBacktest, 
  onStopBacktest,
  onStepForward,
  onStepBackward,
  onToggleAutoPlay,
  isAutoPlaying,
  currentTime,
  isDataLoading
}) {
  const [selectedDate, setSelectedDate] = useState(null);
  const [datePickerOpened, setDatePickerOpened] = useState(false);

  const handleStart = () => {
    if (selectedDate) {
      onStartBacktest(selectedDate);
    }
  };

  const handleStop = () => {
    setSelectedDate(null);
    onStopBacktest();
  };

  return (
    <Stack spacing="xs">
      {!isBacktesting ? (
        <Group position="center">
          <DatePicker
            placeholder="Select start date"
            value={selectedDate}
            onChange={setSelectedDate}
            maxDate={new Date()}
            clearable={false}
            dropdownType="modal"
            opened={datePickerOpened}
            onOpen={() => setDatePickerOpened(true)}
            onClose={() => setDatePickerOpened(false)}
          />
          <Button 
            onClick={handleStart}
            disabled={!selectedDate || isDataLoading}
            loading={isDataLoading}
          >
            Start Backtest
          </Button>
        </Group>
      ) : (
        <Group position="apart" noWrap>
          <Button 
            variant="outline" 
            onClick={onStepBackward}
            disabled={isAutoPlaying || isDataLoading}
            leftIcon={<IconPlayerSkipBack size={16} />}
          >
            Previous
          </Button>
          
          <Button 
            variant="subtle"
            onClick={onToggleAutoPlay}
            disabled={isDataLoading}
            leftIcon={isAutoPlaying ? <IconPlayerPause size={16} /> : <IconPlayerPlay size={16} />}
          >
            {isAutoPlaying ? 'Pause' : 'Play'}
          </Button>
          
          <Button 
            variant="outline" 
            onClick={onStepForward}
            disabled={isAutoPlaying || isDataLoading}
            rightIcon={<IconPlayerSkipForward size={16} />}
          >
            Next
          </Button>
          
          <Text size="sm" color="dimmed">
            {currentTime ? new Date(currentTime).toLocaleString() : 'No data'}
          </Text>
          
          <Button 
            variant="subtle" 
            color="red"
            onClick={handleStop}
            disabled={isDataLoading}
          >
            Stop Backtest
          </Button>
        </Group>
      )}
    </Stack>
  );
}
