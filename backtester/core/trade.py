BUY = 1
SELL = -1
NONE = None

class Trade:
    # minimal dummy Trade class
    def __init__(self, row):
        self.running = True # all new trades we can assume are open
        self.TP = 0
        self.SL = 0
        self.result # the result of the trade -> 1=win, -1=loss
        
    def update(self, row):
        """
        Some logic for checking if the current row is a winner or a lsoer
        """

        # if row.low < self.SL:
        #   close trade 


        pass

    def close_trade():
        pass