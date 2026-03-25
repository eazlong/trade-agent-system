import Summary from "Components/Assistant/Summary";
import { MantineProvider } from '@mantine/core';

const SummaryPage = () => {
  return (
    <MantineProvider withGlobalStyles withNormalizeCSS>
      <div className="h-full w-full">
        <Summary />
      </div>
    </MantineProvider>
  );
};

export default SummaryPage;
