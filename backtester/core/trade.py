BUY = 1
SELL = -1
NONE = None

class Trade:
    # minimal dummy Trade class
    def __init__(self, row):
        self.running = True # all new trades we can assume are open
        self.result = 0 # the result of the trade -> 1=win, -1=loss
        self.SIGNAL = row.SIGNAL
        
    def update(self, row):
        """
        Some logic for checking if the current row is a winner or a lsoer
        """

        if self.SIGNAL == BUY:
            if row.High >= self.TP:
                # win
                self.close_trade(row, 1)
            elif row.Low <= self.SL:
                self.close_trade(row, -1)
                # loss
        if self.SIGNAL == SELL:
            if row.Low <= self.TP:
                # win
                self.close_trade(row, 1)
            elif row.High >= self.SL:
                # loss
                self.close_trade(row, -1)   

        pass

    def close_trade(self, result: int):
        self.running = False
        self.result = result